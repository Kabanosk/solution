"""Explicit additive upgrades of pre-existing Scout schemas, never application re-provisioning."""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from cfo.db import migrate
from cfo.tenancy import LEGACY_ID


@pytest.fixture
def upgrade_db():
    url = os.getenv("TENANT_TEST_DATABASE_URL")
    from urllib.parse import urlparse
    if not url or urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        pytest.skip("set TENANT_TEST_DATABASE_URL to an explicitly selected disposable local database")
    migrate.migrate_auth(url)
    return url


@pytest.fixture(params=["tenant", "legacy"])
def old_scout(upgrade_db, request):
    """Minimal old application tables with sentinel data, including a table full schema replay drops."""
    oid = uuid4() if request.param == "tenant" else LEGACY_ID
    schema = "tenant_" + oid.hex if request.param == "tenant" else "public"
    uid = uuid4()
    tables = ("agent_tasks", "agent_jobs", "transactions", "checkpoints", "events", "audit_log")
    with psycopg.connect(upgrade_db) as conn:
        if schema == "public":
            existing = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'").fetchall()
            assert not existing, "this fixture must not replace existing legacy data"
        else:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name) VALUES (%s,%s,'Upgrade test',%s)",
                         (oid, "upgrade-" + oid.hex, schema))
        conn.execute("INSERT INTO scout_auth.users(id,email,name) VALUES (%s,%s,'Preserved owner')", (uid, f"{uid}@upgrade.test"))
        conn.execute("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES (%s,%s,'owner','owner')", (oid, uid))
        conn.execute("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) VALUES (%s,%s,%s,now()+interval '1 hour')",
                     (hashlib.sha256(uid.bytes).hexdigest(), uid, oid))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        conn.execute("CREATE TABLE agent_tasks(id text PRIMARY KEY, title text); INSERT INTO agent_tasks VALUES ('SC-1','Preserved task')")
        conn.execute("CREATE TABLE agent_jobs(id bigint PRIMARY KEY,kind text,payload jsonb); INSERT INTO agent_jobs VALUES (1,'dm','{}')")
        conn.execute("CREATE TABLE transactions(id text PRIMARY KEY,amount numeric); INSERT INTO transactions VALUES ('payment',-642.80)")
        conn.execute("CREATE TABLE checkpoints(id text PRIMARY KEY,body jsonb); INSERT INTO checkpoints VALUES ('checkpoint','{\"approved\":false}')")
        conn.execute("CREATE TABLE events(id text PRIMARY KEY); INSERT INTO events VALUES ('legacy-event')")
        # Before T050/T052 the decision check allowed only ALLOW/DENY.
        conn.execute("CREATE TABLE audit_log(id bigserial PRIMARY KEY, actor text NOT NULL, action text NOT NULL, "
                     "target text NOT NULL, resource text NOT NULL, "
                     "decision text NOT NULL CHECK (decision IN ('ALLOW','DENY'))); "
                     "INSERT INTO audit_log(actor,action,target,resource,decision) VALUES ('legacy','a','t','r','DENY')")
    try:
        yield upgrade_db, str(oid), schema, str(uid)
    finally:
        with psycopg.connect(upgrade_db) as conn:
            if schema == "public":
                for table in ("dm_replies", "dm_requests", "guardrail_strikes", "guardrail_alerts", "flagged_documents", *tables):
                    conn.execute(sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(sql.Identifier(schema), sql.Identifier(table)))
            else:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
                conn.execute("DELETE FROM scout_auth.organizations WHERE id=%s", (oid,))
            conn.execute("DELETE FROM scout_auth.users WHERE id=%s", (uid,))


def snapshot(url, schema):
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        return {table: conn.execute(sql.SQL("TABLE {}").format(sql.Identifier(table))).fetchall()
                for table in ("agent_tasks", "agent_jobs", "transactions", "checkpoints", "events", "audit_log")}


def assert_dm_schema(conn, schema):
    for table in ("dm_requests", "dm_replies"):
        assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",)).fetchone()[0] is not None
    assert conn.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name='dm_replies' AND column_name='claimed_at'", (schema,)).fetchone()
    assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.agent_jobs_scout_thread_idx",)).fetchone()[0] is not None
    targets = conn.execute("SELECT n.nspname FROM pg_constraint c JOIN pg_class t ON t.oid=c.confrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
                           "WHERE c.connamespace=%s::regnamespace AND c.contype='f' AND c.conrelid IN (to_regclass(%s),to_regclass(%s))",
                           (schema, f"{schema}.dm_requests", f"{schema}.dm_replies")).fetchall()
    assert targets == [(schema,), (schema,)]


GUARDRAIL_TABLES = ("guardrail_strikes", "guardrail_alerts", "flagged_documents")


def assert_guardrails_schema(conn, schema):
    for table in GUARDRAIL_TABLES:
        assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",)).fetchone()[0] is not None, table
    assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.guardrail_strikes_person_idx",)).fetchone()[0] is not None
    conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
    for decision in ("ALLOW", "DENY", "MASK", "BLOCK"):
        conn.execute("INSERT INTO audit_log(actor,action,target,resource,decision) VALUES ('t','a','t','r',%s)", (decision,))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute("INSERT INTO audit_log(actor,action,target,resource,decision) VALUES ('t','a','t','r','MAYBE')")


def test_upgrade_adds_guardrail_storage_and_widens_the_decision_constraint(old_scout):
    url, _, schema, _ = old_scout
    migrate.migrate_auth(url)
    with psycopg.connect(url) as conn:
        assert_guardrails_schema(conn, schema)
    migrate.migrate_auth(url)  # idempotent
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        assert conn.execute("SELECT actor,decision FROM audit_log WHERE actor='legacy'").fetchall() == [("legacy", "DENY")]
        assert conn.execute("SELECT count(*) FROM audit_log WHERE actor='legacy'").fetchone() == (1,)


def test_current_decision_constraint_is_left_alone_on_repeat(old_scout):
    """DROP/ADD CONSTRAINT locks audit_log exclusively and rescans it: the second run must not touch a current one."""
    url, _, schema, _ = old_scout
    migrate.migrate_auth(url)
    query = ("SELECT oid FROM pg_constraint WHERE conrelid=to_regclass(%s) AND conname='audit_log_decision_check'")
    with psycopg.connect(url) as conn:
        (first,) = conn.execute(query, (f"{schema}.audit_log",)).fetchone()
    migrate.migrate_auth(url)
    with psycopg.connect(url) as conn:
        assert conn.execute(query, (f"{schema}.audit_log",)).fetchone() == (first,)  # same object: not recreated


def test_check_does_not_leave_guardrail_tables_behind(old_scout):
    url, _, schema, _ = old_scout
    migrate.migrate_auth(url, check=True)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (f"{schema}.guardrail_strikes",)).fetchone() == (None,)


def test_upgrade_adds_delegated_reply_tables_without_touching_existing_rows(old_scout):
    url, oid, schema, uid = old_scout
    before = snapshot(url, schema)
    with psycopg.connect(url) as conn:
        auth_before = conn.execute("SELECT * FROM scout_auth.sessions WHERE user_id=%s", (uid,)).fetchall()
    migrate.migrate_auth(url)
    with psycopg.connect(url) as conn:
        assert_dm_schema(conn, schema)
        assert conn.execute("SELECT * FROM scout_auth.sessions WHERE user_id=%s", (uid,)).fetchall() == auth_before
        assert conn.execute("SELECT employee_id FROM scout_auth.memberships WHERE organization_id=%s AND user_id=%s", (oid, uid)).fetchone() == ("owner",)
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        rid = conn.execute("INSERT INTO dm_requests(task_id,requester_id,recipient_id,thread_ref,text) VALUES ('SC-1','owner','peer','D:1','Question') RETURNING id").fetchone()[0]
        conn.execute("INSERT INTO dm_replies(request_id,event_key,sender_id,text,status) VALUES (%s,'event','peer','Answer','acked')", (rid,))
    migrate.migrate_auth(url)  # a repeated deployment retains both old and new rows
    with psycopg.connect(url) as conn:
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        assert conn.execute("SELECT text,status FROM dm_replies").fetchall() == [("Answer", "acked")]
    assert snapshot(url, schema) == before


def test_check_rolls_back_existing_schema_upgrade_and_provision_function(old_scout):
    url, _, schema, _ = old_scout
    before = snapshot(url, schema)
    with psycopg.connect(url) as conn:
        function_before = conn.execute("SELECT pg_get_functiondef('scout_auth.provision_organization(uuid)'::regprocedure)").fetchone()[0]
    migrate.migrate_auth(url, check=True)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT to_regclass(%s),to_regclass(%s),to_regclass(%s)",
                            (f"{schema}.dm_requests", f"{schema}.dm_replies", f"{schema}.agent_jobs_scout_thread_idx")).fetchone() == (None, None, None)
        assert conn.execute("SELECT pg_get_functiondef('scout_auth.provision_organization(uuid)'::regprocedure)").fetchone()[0] == function_before
    assert snapshot(url, schema) == before
    migrate.migrate_auth(url)  # unlike --check, --migrate actually installs the tables
    with psycopg.connect(url) as conn:
        assert_dm_schema(conn, schema)


def test_auth_only_database_does_not_get_legacy_application_tables(upgrade_db):
    with psycopg.connect(upgrade_db) as conn:
        assert conn.execute("SELECT to_regclass('public.agent_tasks')").fetchone() == (None,)
    migrate.migrate_auth(upgrade_db, check=True)
    migrate.migrate_auth(upgrade_db)
    with psycopg.connect(upgrade_db) as conn:
        assert conn.execute("SELECT to_regclass('public.agent_tasks'),to_regclass('public.dm_requests')").fetchone() == (None, None)


def test_registered_new_tenant_still_provisions_current_delegated_reply_schema(upgrade_db):
    oid = uuid4()
    schema = "tenant_" + oid.hex
    try:
        migrate.migrate_auth(upgrade_db)
        with psycopg.connect(upgrade_db) as conn:
            conn.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name) VALUES (%s,%s,'New tenant',%s)", (oid, "upgrade-" + oid.hex, schema))
            conn.execute("SELECT scout_auth.provision_organization(%s)", (oid,))
            assert_dm_schema(conn, schema)
            assert_guardrails_schema(conn, schema)
            assert conn.execute(sql.SQL("SELECT count(*) FROM {}.transactions").format(sql.Identifier(schema))).fetchone() == (0,)
    finally:
        with psycopg.connect(upgrade_db) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            conn.execute("DELETE FROM scout_auth.organizations WHERE id=%s", (oid,))


def test_upgrade_rejects_forged_schema_registration_and_rolls_back(upgrade_db, monkeypatch):
    oid = uuid4()
    connect = psycopg.connect
    with connect(upgrade_db) as conn, conn.transaction(force_rollback=True):
        # Model a corrupted old registry without ever committing its invalid row or constraint change.
        conn.execute("ALTER TABLE scout_auth.organizations DROP CONSTRAINT organization_schema_isolation")
        conn.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name) VALUES (%s,%s,'Forged',%s)", (oid, "forged-" + oid.hex, "tenant_" + uuid4().hex))
        @contextmanager
        def same_transaction(*args, **kwargs):
            yield conn
        monkeypatch.setattr(migrate.psycopg, "connect", same_transaction)
        with pytest.raises(PermissionError, match="Invalid organization schema registration"):
            migrate.migrate_auth(upgrade_db)
