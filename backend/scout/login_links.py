"""One-click dashboard sign-in from Slack: "send me a link to the dashboard".

`create` makes a one-use token (15 minutes) for the Scout account of an employee and returns
`<dashboard>/login/<org>/<token>`; the dashboard page exchanges it for a session (`AuthStore.consumeLoginLink`).
Only the SHA-256 of the token is stored. Someone with no login yet gets their Slack join link instead.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

import psycopg

log = logging.getLogger(__name__)
TTL_MINUTES = 15
_DDL = ("CREATE TABLE IF NOT EXISTS scout_auth.login_links (token_hash text PRIMARY KEY, "
        "user_id uuid NOT NULL REFERENCES scout_auth.users(id) ON DELETE CASCADE, "
        "organization_id uuid NOT NULL REFERENCES scout_auth.organizations(id) ON DELETE CASCADE, "
        "expires_at timestamptz NOT NULL)")


def organization(database_url: str, organization_id: str) -> tuple[str, str] | None:
    """(slug, name) of an organization."""
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        row = conn.execute("SELECT slug, name FROM scout_auth.organizations WHERE id = %s",
                           (organization_id,)).fetchone()
    return (row[0], row[1]) if row else None


def create(database_url: str, dashboard_url: str, organization_id: str, employee_id: str) -> str | None:
    """A one-use sign-in URL for the member of `organization_id` whose employee is `employee_id`; None if none."""
    token = secrets.token_hex(32)  # hex: nothing in it that Slack formatting could mangle
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        try:
            conn.execute(_DDL)
            conn.commit()
        except Exception:  # noqa: BLE001 - no DDL rights: the auth migration created it
            conn.rollback()
        member = conn.execute(
            "SELECT m.user_id, o.slug FROM scout_auth.memberships m JOIN scout_auth.organizations o "
            "ON o.id = m.organization_id WHERE m.organization_id = %s AND m.employee_id = %s LIMIT 1",
            (organization_id, employee_id)).fetchone()
        if member is None:
            return None
        conn.execute("DELETE FROM scout_auth.login_links WHERE expires_at < now()")
        conn.execute("INSERT INTO scout_auth.login_links (token_hash, user_id, organization_id, expires_at) "
                     f"VALUES (%s, %s, %s, now() + interval '{TTL_MINUTES} minutes')",
                     (hashlib.sha256(token.encode()).hexdigest(), member[0], organization_id))
        conn.commit()
    return f"{dashboard_url.rstrip('/')}/login/{member[1]}/{token}"
