"""Operator-only schema tooling. No startup migration and no automatic legacy enrollment.

    python -m cfo.db.migrate --migrate          # auth + provisioning + additive existing Scout upgrades (dm replies, guardrails)
    python -m cfo.db.migrate --provision UUID   # organization row must already exist
    python -m cfo.db.migrate --check            # rollback auth migration, change nothing

Next signup inserts the organization then calls scout_auth.provision_organization(uuid) in the same
transaction. It then inserts employees(id,name,role='CEO') and the membership. Existing public data is
registered but not altered; an operator must explicitly enroll a verified existing owner.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql

from cfo.config import get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
AUTH_PATH = Path(__file__).with_name("auth.sql")
SCOUT_UPGRADE_PATHS = (
    Path(__file__).with_name("scout_dm_replies.sql"),
    Path(__file__).with_name("scout_guardrails.sql"),
    Path(__file__).with_name("scout_slack_members.sql"),
    Path(__file__).with_name("scout_strategy.sql"),
)


def apply_schema(database_url: str, *, check: bool = False, schema: str | None = None) -> None:
    """Explicit operator/test application-table helper; requires an explicit or bound schema."""
    from cfo.tenancy import connect
    with connect(database_url, schema=schema) as conn:
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        if check:
            conn.rollback()


def _application_ddl() -> str:
    from langgraph.checkpoint.postgres import PostgresSaver
    scout = (Path(__file__).resolve().parents[2] / "scout" / "schema.sql").read_text(encoding="utf-8")
    # Provisioning only creates tables. Never replay the legacy cleanup DROP against any existing schema.
    scout = "\n".join(line for line in scout.splitlines() if not line.startswith("DROP TABLE"))
    ddl = SCHEMA_PATH.read_text(encoding="utf-8") + "\n" + scout
    for version, migration in enumerate(PostgresSaver.MIGRATIONS):
        # CREATE INDEX CONCURRENTLY cannot run inside signup's transaction. Empty tables need no concurrency.
        ddl += "\n" + migration.replace("CREATE INDEX CONCURRENTLY", "CREATE INDEX")
        ddl += f"\nINSERT INTO checkpoint_migrations(v) VALUES ({version}) ON CONFLICT DO NOTHING;"
    return ddl


def _upgrade_scout_schemas(conn: psycopg.Connection) -> None:
    """Add delegated-reply and guardrails storage only to registered, already initialized application schemas."""
    from cfo.tenancy import Tenant

    ddl = "\n".join(path.read_text(encoding="utf-8") for path in SCOUT_UPGRADE_PATHS)
    previous_path = conn.execute("SHOW search_path").fetchone()[0]
    organizations = conn.execute(
        "SELECT id,slug,schema_name,legacy FROM scout_auth.organizations ORDER BY id FOR UPDATE"
    ).fetchall()
    for oid, slug, schema, legacy in organizations:
        # Treat registry data as a claim, not authority to run DDL against an arbitrary schema.
        Tenant(id=str(oid), slug=slug, schema_name=schema, legacy=legacy, employee_id=None, role="operator")
        tables = {r[0] for r in conn.execute(
            "SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relname IN ('agent_tasks','agent_jobs') AND c.relkind IN ('r','p')",
            (schema,)).fetchall()}
        if not tables:
            continue  # auth-only databases (including an uninitialized legacy public schema) stay empty
        if tables != {"agent_tasks", "agent_jobs"}:
            raise ValueError(f"Incomplete Scout schema: {schema}")
        # No public fallback: new tables, foreign keys, indexes and sequence defaults remain tenant-local.
        conn.execute("SELECT set_config('search_path',%s,true)", (sql.Identifier(schema).as_string(conn),))
        conn.execute(ddl)
    conn.execute("SELECT set_config('search_path',%s,true)", (previous_path,))


def migrate_auth(database_url: str, *, check: bool = False) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute(AUTH_PATH.read_text(encoding="utf-8"))
        # Canonical CREATE DDL is parsed in the fresh schema, NOT LIKE public (which shares defaults/FKs).
        # SECURITY INVOKER: the web DB role must have DDL rights; no privilege escalation for other roles.
        function = sql.SQL("""
CREATE OR REPLACE FUNCTION scout_auth.provision_organization(org uuid) RETURNS void
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pg_catalog AS $provision$
DECLARE target text; is_legacy boolean; previous_path text;
BEGIN
    SELECT schema_name, legacy INTO target, is_legacy
      FROM scout_auth.organizations WHERE id = org FOR UPDATE;
    IF NOT FOUND OR is_legacy OR org = '00000000-0000-4000-8000-000000000001'::uuid
       OR target <> 'tenant_' || replace(org::text, '-', '') THEN
        RAISE EXCEPTION 'Organization cannot be provisioned' USING ERRCODE = '42501';
    END IF;
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = target) THEN
        RAISE EXCEPTION 'Organization schema already exists' USING ERRCODE = '42P06';
    END IF;
    EXECUTE format('CREATE SCHEMA %I', target);
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', target);
    previous_path := current_setting('search_path');
    PERFORM set_config('search_path', quote_ident(target), true);
    EXECUTE {ddl};
    PERFORM set_config('search_path', previous_path, true);
END
$provision$;
REVOKE ALL ON FUNCTION scout_auth.provision_organization(uuid) FROM PUBLIC;
""").format(ddl=sql.Literal(_application_ddl()))
        conn.execute(function)
        _upgrade_scout_schemas(conn)
        if check:
            conn.rollback()


def provision(database_url: str, organization_id: str) -> None:
    oid = UUID(organization_id)
    with psycopg.connect(database_url) as conn:
        conn.execute("SELECT scout_auth.provision_organization(%s)", (oid,))


def enroll_legacy_owner(database_url: str, email: str, *, confirm: bool = False) -> None:
    """Operator asserts the intended existing identity. Never create users, guess emails or run at startup."""
    if not confirm:
        raise PermissionError("Explicit --confirm-legacy-owner is required after verifying the intended owner")
    from cfo.tenancy import LEGACY_ID
    with psycopg.connect(database_url) as conn:
        org = conn.execute("SELECT schema_name,slug,legacy FROM scout_auth.organizations WHERE id=%s FOR UPDATE", (LEGACY_ID,)).fetchone()
        if org != ("public", "pizzeria-amalfi", True):
            raise PermissionError("Reserved legacy organization registration is invalid")
        users = conn.execute("SELECT id FROM scout_auth.users WHERE lower(email)=lower(%s) FOR UPDATE", (email.strip(),)).fetchall()
        if len(users) != 1:
            raise PermissionError("Owner must be one explicitly verified preexisting user")
        uid = users[0][0]
        other = conn.execute("SELECT user_id FROM scout_auth.memberships WHERE organization_id=%s AND role='owner' AND user_id<>%s", (LEGACY_ID, uid)).fetchone()
        if other:
            raise PermissionError("A different legacy owner is already enrolled; resolve manually")
        employee = conn.execute("SELECT role FROM public.employees WHERE id='vincenzo'").fetchone()
        if not employee or not any(s in employee[0].casefold() for s in ("ceo", "owner")):
            raise PermissionError("Existing legacy owner employee vincenzo is not present")
        conn.execute("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES (%s,%s,'owner','vincenzo') "
                     "ON CONFLICT (organization_id,user_id) DO UPDATE SET role='owner',employee_id='vincenzo'", (LEGACY_ID, uid))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfo.db.migrate")
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--migrate", action="store_true", help="install auth/provisioning and upgrade initialized registered Scout schemas")
    actions.add_argument("--provision", metavar="UUID", help="initialize a registered nonlegacy organization")
    actions.add_argument("--check", action="store_true", help="verify migration in a rolled-back transaction")
    actions.add_argument("--enroll-owner-email", metavar="ADDRESS", help="operator-only enrollment of a verified existing legacy owner")
    parser.add_argument("--confirm-legacy-owner", action="store_true", help="explicitly attest the selected email is the intended Amalfi owner")
    args = parser.parse_args(argv)
    if args.confirm_legacy_owner and not args.enroll_owner_email:
        parser.error("--confirm-legacy-owner requires --enroll-owner-email")
    try:
        if args.enroll_owner_email:
            enroll_legacy_owner(get_settings().database_url, args.enroll_owner_email, confirm=args.confirm_legacy_owner)
        elif args.provision:
            provision(get_settings().database_url, args.provision)
        else:
            migrate_auth(get_settings().database_url, check=args.check)
    except (psycopg.Error, ValueError, PermissionError) as exc:
        print(f"schema failed: {exc}", file=sys.stderr)
        return 1
    print("schema OK (dry run)" if args.check else "schema applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
