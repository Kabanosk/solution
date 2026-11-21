"""Non-fixture helpers for the onboarding tests (kept out of conftest: several conftest modules share that name)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

import httpx
import psycopg
from psycopg.rows import dict_row

APP_ORIGIN = "https://scout.example.test"
MASTER_KEY = "m" * 44  # any 32+ character server secret; per-organization keys are derived from it


@dataclass
class Org:
    id: str
    slug: str
    schema: str
    token: str
    member_token: str | None = None

    def headers(self, member: bool = False) -> dict[str, str]:
        return {"Authorization": "Bearer " + (self.member_token if member else self.token),
                "X-Scout-Organization": self.slug}


def _make_org(url: str, *, with_setup_row: bool, with_member: bool) -> Org:
    oid = uuid4()
    schema = "tenant_" + oid.hex
    token = uuid4().hex + uuid4().hex
    member_token = uuid4().hex + uuid4().hex if with_member else None
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name) VALUES (%s,%s,%s,%s)",
                     (oid, f"test-{oid.hex}", "Org", schema))
        conn.execute("SELECT scout_auth.provision_organization(%s)", (oid,))
        conn.execute("INSERT INTO scout_auth.users(id,email,name) VALUES (%s,%s,%s)", (oid, f"{oid}@example.test", "Owner"))
        conn.execute("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES (%s,%s,'owner',%s)",
                     (oid, oid, str(oid)))
        conn.execute("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) "
                     "VALUES (%s,%s,%s,now()+interval '1 hour')", (hashlib.sha256(token.encode()).hexdigest(), oid, oid))
        conn.execute(f'INSERT INTO "{schema}".employees(id,name,role) VALUES (%s,%s,\'CEO\')', (str(oid), "Owner"))
        if member_token:
            mid = uuid4()
            conn.execute("INSERT INTO scout_auth.users(id,email,name) VALUES (%s,%s,%s)", (mid, f"{mid}@example.test", "Member"))
            conn.execute("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES (%s,%s,'member',%s)",
                         (oid, mid, str(mid)))
            conn.execute("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) "
                         "VALUES (%s,%s,%s,now()+interval '1 hour')", (hashlib.sha256(member_token.encode()).hexdigest(), mid, oid))
        if with_setup_row:  # exactly what the Next signup transaction inserts
            conn.execute("INSERT INTO scout_auth.workspace_setup(organization_id) VALUES (%s)", (oid,))
    return Org(str(oid), f"test-{oid.hex}", schema, token, member_token)


def _drop_org(url: str, org: Org) -> None:
    with psycopg.connect(url) as conn:
        conn.execute(f'DROP SCHEMA "{org.schema}" CASCADE')
        conn.execute("DELETE FROM scout_auth.organizations WHERE id=%s", (org.id,))
        conn.execute("DELETE FROM scout_auth.users WHERE email LIKE %s OR id=%s", (f"%{org.id}%", org.id))
        conn.execute("DELETE FROM scout_auth.users WHERE id IN (SELECT user_id FROM scout_auth.memberships "
                     "WHERE organization_id=%s)", (org.id,))


class ProviderFakes:
    """Scripted external HTTP. An unscripted request fails the test: no live provider call can slip through."""

    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self._routes: list[tuple[str, str, str, object]] = []

    def route(self, method: str, host: str, path: str, response: object) -> None:
        self._routes.insert(0, (method.upper(), host, path, response))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for method, host, path, response in self._routes:
            if request.method == method and request.url.host == host and request.url.path == path:
                result = response(request) if callable(response) else response
                if isinstance(result, httpx.Response):
                    return result
                status, body = result if isinstance(result, tuple) else (200, result)
                return httpx.Response(status, json=body)
        raise AssertionError(f"unscripted provider request: {request.method} {request.url}")

    def hosts(self) -> set[str]:
        return {c.url.host for c in self.calls}

    def requests(self, method: str | None = None, host: str | None = None) -> list[httpx.Request]:
        return [c for c in self.calls if (method is None or c.method == method) and (host is None or c.url.host == host)]

    def bodies(self) -> str:
        return "\n".join((c.content or b"").decode("utf-8", "replace") for c in self.calls)


def admin(url: str) -> psycopg.Connection:
    return psycopg.connect(url, row_factory=dict_row, autocommit=True)


def setup_row(url: str, org_id: str) -> dict | None:
    with admin(url) as conn:
        return conn.execute("SELECT * FROM scout_auth.workspace_setup WHERE organization_id=%s", (org_id,)).fetchone()


def tenant_rows(url: str, org: Org, sql: str, params: tuple = ()) -> list[dict]:
    with admin(url) as conn:
        conn.execute(f'SET search_path = "{org.schema}"')
        return conn.execute(sql, params).fetchall()


def step(setup: dict, step_id: str) -> dict:
    return next(s for s in setup["steps"] if s["id"] == step_id)


def dump(obj: object) -> str:
    return json.dumps(obj, default=str)

