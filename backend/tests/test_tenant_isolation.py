"""Security regressions. Integration tests use only an explicitly supplied disposable local DB."""
from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from cfo.api.app import create_app


@pytest.mark.parametrize("path", ["/", "/docs", "/docs/", "/redoc", "/openapi.json", "/v1/tasks", "/v1/tasks/stream", "/v1/kpis", "/v1/connections/google/callback", "/v1/nothing"])
def test_every_non_health_route_requires_session(path):
    response = TestClient(create_app()).get(path, follow_redirects=False)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_multi_tenant_api_never_enables_global_mlflow_autolog(monkeypatch):
    import importlib
    module = importlib.import_module("cfo.api.app")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "https://shared-mlflow.invalid")
    def forbidden(*args, **kwargs):
        pytest.fail("Multi-tenant API attempted global MLflow tracing/autolog initialization")
    monkeypatch.setattr("cfo.observability.init_tracing", forbidden)
    monkeypatch.setattr(module, "init_tracing", forbidden, raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200


def test_shared_write_token_does_not_grant_web_access(monkeypatch):
    monkeypatch.setenv("API_WRITE_TOKEN", "old-shared-secret")
    response = TestClient(create_app()).post("/v1/chat", headers={"Authorization": "Bearer old-shared-secret"}, json={"messages": [{"role": "user", "content": "hello"}]})
    assert response.status_code == 401


def test_chat_owner_and_usage_remain_scoped_in_background_thread(organizations, tenant_db, monkeypatch):
    import json
    from cfo.api import auth
    from cfo.llm import FakeLLM
    from cfo.tenancy import current_tenant
    from cfo.usage import recorder
    monkeypatch.setattr(auth, "database_url", lambda: tenant_db)
    seen = []
    def answer(messages, tools):
        tenant = current_tenant()
        seen.append(tenant.id)
        assert "Pizzeria Amalfi" not in messages[0].content
        if not any(m.role == "tool" for m in messages):
            return FakeLLM.tool_call("who_is", person="me")
        return "Your owner identity is " + tenant.employee_id
    monkeypatch.setattr("cfo.llm.get_llm", lambda: FakeLLM(handler=answer))
    recorder.set_sink(recorder.DbSink())
    client = TestClient(create_app())
    try:
        for a in organizations:
            response = client.post("/v1/chat", headers=_headers(a), json={"conversation_id": "sameconversation", "messages": [{"role": "user", "content": "Who am I?"}]})
            assert response.status_code == 200
            events = [json.loads(line) for line in response.text.splitlines()]
            assert events[-1]["type"] == "done"
            assert a[0] in next(e["text"] for e in events if e["type"] == "delta")
            with psycopg.connect(tenant_db, row_factory=dict_row) as conn:
                assert conn.execute(f'SELECT count(*) AS n FROM "{a[2]}".llm_usage').fetchone()["n"] == 2
                assert conn.execute(f'SELECT requested_by FROM "{a[2]}".agent_tasks').fetchone()["requested_by"] == a[0]
        assert seen == [organizations[0][0]] * 2 + [organizations[1][0]] * 2
    finally:
        recorder.set_sink(None)


def test_notifications_use_tenant_channels(organizations, tenant_db):
    from scout.db import connect, notify
    a, b = organizations
    with connect(tenant_db, a[2], autocommit=True) as ca, connect(tenant_db, b[2], autocommit=True) as cb:
        ca.execute(f'LISTEN "scout_tasks_{a[2]}"')
        cb.execute(f'LISTEN "scout_tasks_{b[2]}"')
        notify(ca, "scout_tasks", "private-task")
        assert [n.payload for n in ca.notifies(timeout=0.1)] == ["private-task"]
        assert list(cb.notifies(timeout=0.1)) == []


def test_unbound_database_factories_fail_closed():
    from scout.db import factory
    with pytest.raises(PermissionError):
        factory("postgresql://unused")


@pytest.fixture(scope="module")
def tenant_db():
    url = os.getenv("TENANT_TEST_DATABASE_URL")
    if not url or not ("127.0.0.1" in url or "localhost" in url):
        pytest.skip("set TENANT_TEST_DATABASE_URL to a disposable local DB")
    from cfo.db.migrate import migrate_auth
    migrate_auth(url)
    yield url


@pytest.fixture
def organizations(tenant_db):
    ids = [uuid4(), uuid4()]
    tokens = [uuid4().hex + uuid4().hex for _ in ids]
    with psycopg.connect(tenant_db, row_factory=dict_row) as conn:
        for i, oid in enumerate(ids):
            schema = "tenant_" + oid.hex
            conn.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name) VALUES (%s,%s,%s,%s)", (oid, f"test-{oid.hex}", f"Org {i}", schema))
            conn.execute("SELECT scout_auth.provision_organization(%s)", (oid,))
            conn.execute("INSERT INTO scout_auth.users(id,email,name) VALUES (%s,%s,%s)", (oid, f"{oid}@example.test", f"Owner {i}"))
            conn.execute("INSERT INTO scout_auth.memberships(organization_id,user_id,role,employee_id) VALUES (%s,%s,'owner',%s)", (oid, oid, str(oid)))
            conn.execute("INSERT INTO scout_auth.sessions(token_hash,user_id,organization_id,expires_at) VALUES (%s,%s,%s,now()+interval '1 hour')", (hashlib.sha256(tokens[i].encode()).hexdigest(), oid, oid))
            conn.execute(f'INSERT INTO "{schema}".employees(id,name,role) VALUES (%s,%s,\'CEO\')', (str(oid), f"Owner {i}"))
    yield [(str(oid), f"test-{oid.hex}", "tenant_" + oid.hex, tokens[i]) for i, oid in enumerate(ids)]
    with psycopg.connect(tenant_db) as conn:
        for oid in ids:
            conn.execute(f'DROP SCHEMA "tenant_{oid.hex}" CASCADE')
            conn.execute("DELETE FROM scout_auth.organizations WHERE id=%s", (oid,))
            conn.execute("DELETE FROM scout_auth.users WHERE id=%s", (oid,))


def test_new_tenant_connectors_never_inherit_global_files_or_slack(organizations, tenant_db, tmp_path):
    from cfo.tenancy import Tenant, bind_tenant
    from scout.chat_api import build_context
    from scout.config import ScoutSettings
    from cfo.llm import FakeLLM
    from unittest.mock import patch
    a = organizations[0]
    settings = ScoutSettings(database_url=tenant_db, slack_bot_token="DO-NOT-USE", slack_user_map="vincenzo=SHARED", workstations_root=str(tmp_path))
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "Documents").mkdir()
    (tmp_path / "private" / "Documents" / "secret.txt").write_text("other organization secret")
    tenant = Tenant(id=a[0], slug=a[1], schema_name=a[2], employee_id=a[0], role="owner")
    with bind_tenant(tenant), patch("cfo.llm.get_llm", return_value=FakeLLM()):
        ctx = build_context(settings)
        assert ctx.connectors.files.search("private", ["secret"]) == []
        assert ctx.connectors.files.name == "not_configured"
        assert ctx.connectors.bank.name == "not_configured"
        assert not ctx.connectors.mail.available()
        assert ctx.settings.slack_bot_token == ""
        assert ctx.settings.ceo_id == a[0]
        assert ctx.hr.role(a[0]) == "ceo"


def test_usage_sink_failure_circuit_is_per_organization(organizations, tenant_db):
    from datetime import datetime, timezone
    from cfo.tenancy import Tenant, bind_tenant, connect, current_tenant
    from cfo.usage.recorder import DbSink, UsageRecord
    a, b = organizations
    def db():
        if current_tenant().id == a[0]:
            raise psycopg.OperationalError("one organization's DB write failed")
        return connect(tenant_db)
    sink = DbSink(connect=db, cooldown=1000)
    record = UsageRecord(ts=datetime.now(timezone.utc), model="fake", prompt_tokens=1, completion_tokens=1, latency_ms=1)
    for org in organizations:
        with bind_tenant(Tenant(id=org[0],slug=org[1],schema_name=org[2],employee_id=org[0],role="owner")):
            sink.write(record)
    with psycopg.connect(tenant_db) as conn:
        assert conn.execute(f'SELECT count(*) FROM "{b[2]}".llm_usage').fetchone()[0] == 1


def test_dm_router_prompt_is_generic_for_new_tenant(organizations):
    from cfo.llm import FakeLLM
    from cfo.tenancy import Tenant, bind_tenant
    from scout.llm_tasks import route_dm
    a = organizations[0]
    seen = []
    def handler(messages, tools):
        seen.append(messages[0].content)
        return FakeLLM.tool_call("route_message", intent="smalltalk")
    with bind_tenant(Tenant(id=a[0],slug=a[1],schema_name=a[2],employee_id=a[0],role="owner")):
        route_dm(FakeLLM(handler=handler), "hello")
    assert seen and "Pizzeria Amalfi" not in seen[0]


def test_model_paths_and_registry_are_tenant_private(organizations, monkeypatch, tmp_path):
    from cfo.tenancy import Tenant, bind_tenant
    from cfo.forecast.model import default_model_path
    from cfo.api.services import _mlflow_model
    monkeypatch.setenv("FORECAST_MODEL_PATH", str(tmp_path / "global-model.json"))
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://must-not-be-used.test")
    paths = []
    for a in organizations:
        with bind_tenant(Tenant(id=a[0], slug=a[1], schema_name=a[2], employee_id=a[0], role="owner")):
            path = default_model_path()
            assert a[0] in str(path)
            assert path != tmp_path / "global-model.json"
            assert _mlflow_model() is None
            paths.append(path)
    assert paths[0] != paths[1]


def test_worker_uses_explicit_uuid_owner_and_never_migrates(organizations, tenant_db, monkeypatch):
    from scout.worker import build
    from scout.config import ScoutSettings
    from cfo.llm import FakeLLM
    a = organizations[0]
    monkeypatch.setenv("SCOUT_ORGANIZATION_ID", a[0])
    monkeypatch.setattr("cfo.llm.get_llm", lambda: FakeLLM(handler=lambda messages, tools: FakeLLM.tool_call("route_message", intent="smalltalk")))
    def no_migration(*args, **kwargs):
        pytest.fail("Worker startup attempted a schema migration")
    monkeypatch.setattr("scout.db.apply_schema", no_migration)
    monkeypatch.setattr("cfo.observability.init_tracing", no_migration)
    worker, socket = build(ScoutSettings(database_url=tenant_db))
    assert worker.tenant.id == a[0]
    assert worker.ctx.settings.ceo_id == a[0]
    assert worker.ctx.connectors.bank.name == "not_configured"
    assert socket is None
    worker.ctx.jobs.enqueue("dm", {"from_id": a[0], "text": "hello"}, "hello")
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(worker.run_one).result()
    assert "Pizzeria Amalfi" not in worker.ctx.notifier.sent[-1]["text"]
    worker.ctx.checkpointer.conn.close()


def test_legacy_enrollment_requires_operator_confirmation_and_existing_user(tenant_db):
    from cfo.db.migrate import enroll_legacy_owner
    with pytest.raises(PermissionError):
        enroll_legacy_owner(tenant_db, "nobody@example.test", confirm=False)
    with pytest.raises(PermissionError):
        enroll_legacy_owner(tenant_db, "nobody@example.test", confirm=True)
    with psycopg.connect(tenant_db) as conn:
        assert conn.execute("SELECT count(*) FROM scout_auth.users WHERE email='nobody@example.test'").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM scout_auth.memberships WHERE organization_id='00000000-0000-4000-8000-000000000001'").fetchone()[0] == 0


def test_worker_requires_explicit_organization(monkeypatch):
    from scout.worker import build
    monkeypatch.delenv("SCOUT_ORGANIZATION", raising=False)
    with pytest.raises(PermissionError):
        build()


def test_standalone_mock_bank_has_no_anonymous_bypass():
    from scout.mock_bank.app import app
    client = TestClient(app)
    assert client.get("/api/1.0/accounts").status_code == 401
    assert client.get("/docs").status_code == 401
    assert client.post("/mock/transactions", json={}).status_code == 401
    assert client.get("/healthz").status_code == 200


def test_mcp_http_transport_is_disabled(monkeypatch):
    from cfo.mcp_servers.__main__ import main
    from mcp.server.fastmcp import FastMCP
    def no_server(*args, **kwargs):
        pytest.fail("Unauthenticated MCP HTTP transport started")
    monkeypatch.setattr(FastMCP, "run", no_server)
    with pytest.raises(SystemExit):
        main(["company-db", "--transport", "streamable-http"])


def _headers(org):
    return {"Authorization": "Bearer " + org[3], "X-Scout-Organization": org[1]}


def test_real_sessions_membership_expiry_and_wrong_slug(organizations, tenant_db, monkeypatch):
    from cfo.api import auth
    monkeypatch.setattr(auth, "database_url", lambda: tenant_db)
    a, b = organizations
    client = TestClient(create_app())
    assert client.get("/v1/tasks", headers=_headers(a)).status_code == 200
    assert client.get("/v1/tasks", headers={**_headers(a), "X-Scout-Organization": b[1]}).status_code == 401
    with psycopg.connect(tenant_db) as conn:
        conn.execute("UPDATE scout_auth.sessions SET expires_at=now()-interval '1 second' WHERE user_id=%s", (a[0],))
    assert client.get("/v1/tasks", headers=_headers(a)).status_code == 401
    with psycopg.connect(tenant_db) as conn:
        conn.execute("DELETE FROM scout_auth.memberships WHERE user_id=%s", (b[0],))
    assert client.get("/v1/tasks", headers=_headers(b)).status_code == 401


def test_two_org_same_ids_and_immutable_thread_factory(organizations, tenant_db):
    from cfo.tenancy import Tenant, bind_tenant
    from scout.db import factory
    a, b = organizations
    tenants = [Tenant(id=o[0], slug=o[1], schema_name=o[2], employee_id=o[0], role="owner", legacy=False) for o in organizations]
    factories = []
    for i, tenant in enumerate(tenants):
        with bind_tenant(tenant):
            cf = factory(tenant_db)
        with cf() as conn:
            conn.execute("INSERT INTO agent_tasks(id,dedupe_key,loop,title) VALUES ('same','same','chat',%s)", (f"private-{i}",))
        factories.append(cf)
    def title(cf):
        with cf() as conn:
            return conn.execute("SELECT title FROM agent_tasks WHERE id='same'").fetchone()["title"]
    with bind_tenant(tenants[1]), ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(title, factories)) == ["private-0", "private-1"]
    with pytest.raises(FrozenInstanceError):
        tenants[0].schema_name = b[2]
    with bind_tenant(tenants[0]), pytest.raises(PermissionError):
        factory(tenant_db, b[2])


def test_provision_is_empty_private_and_self_contained(organizations, tenant_db):
    a, b = organizations
    with psycopg.connect(tenant_db, row_factory=dict_row) as conn:
        legacy = conn.execute("SELECT * FROM scout_auth.organizations WHERE slug='pizzeria-amalfi'").fetchone()
        assert str(legacy["id"]) == "00000000-0000-4000-8000-000000000001"
        assert legacy["schema_name"] == "public" and legacy["legacy"]
        assert conn.execute("SELECT count(*) AS n FROM scout_auth.memberships WHERE organization_id=%s", (legacy["id"],)).fetchone()["n"] == 0
        for schema in (a[2], b[2]):
            for table in ("connections", "connection_credentials", "transactions", "agent_tasks", "llm_usage", "checkpoints"):
                assert conn.execute(f'SELECT count(*) AS n FROM "{schema}".{table}').fetchone()["n"] == 0
            fks = conn.execute("SELECT confrelid::regclass::text AS target FROM pg_constraint WHERE connamespace=%s::regnamespace AND contype='f'", (schema,)).fetchall()
            assert all(r["target"].startswith(schema + ".") for r in fks)
            conn.execute(f'INSERT INTO "{schema}".agent_tasks(dedupe_key,loop,title) VALUES (\'new\',\'chat\',\'New\')')
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("INSERT INTO scout_auth.organizations(id,slug,name,schema_name,legacy) VALUES (%s,'claim','Bad','public',true)", (uuid4(),))


def test_api_cross_tenant_ids_not_visible_and_mutations_disabled(organizations, tenant_db, monkeypatch):
    from cfo.api import auth
    monkeypatch.setattr(auth, "database_url", lambda: tenant_db)
    a, b = organizations
    with psycopg.connect(tenant_db) as conn:
        conn.execute(f'INSERT INTO "{a[2]}".agent_tasks(id,dedupe_key,loop,title) VALUES (\'private-id\',\'private\',\'chat\',\'secret\')')
    client = TestClient(create_app())
    assert client.get("/v1/tasks/private-id", headers=_headers(a)).status_code == 200
    assert client.get("/v1/tasks/private-id", headers=_headers(b)).status_code == 404
    for path in ("/v1/connections/google/start", "/v1/connections/google/callback"):
        assert client.get(path, headers=_headers(a)).status_code == 403
    for path in ("/v1/connections/gmail:enable", "/v1/demo/play/ksef", "/v1/demo/play/rent"):
        assert client.post(path, headers=_headers(a)).status_code == 403
