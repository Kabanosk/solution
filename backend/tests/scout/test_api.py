from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cfo.api.errors import install_error_handlers
from cfo.api.settings import get_api_settings
from scout import api as scout_api
from scout.config import ScoutSettings, get_scout_settings
from scout.connectors.crypto import new_key, open_
from scout.tasks import TaskRecorder

SECRET = "state-secret"


@pytest.fixture
def client(conn_factory, monkeypatch):  # noqa: ANN001
    key = new_key()
    settings = ScoutSettings(database_url="postgresql://x@localhost/x", google_client_id="cid", google_client_secret="cs",
                             connections_key=key, oauth_state_secret=SECRET, dashboard_url="https://dash.test",
                             scout_demo=False)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(scout_api.router)

    def conn_override():
        conn = conn_factory()
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[scout_api.get_conn] = conn_override
    app.dependency_overrides[get_scout_settings] = lambda: settings
    monkeypatch.setenv("API_WRITE_TOKEN", "tok")
    get_api_settings.cache_clear()
    c = TestClient(app)
    c.settings, c.key = settings, key  # type: ignore[attr-defined]
    yield c
    get_api_settings.cache_clear()


AUTH = {"Authorization": "Bearer tok"}


def test_tasks_list_counts(client, conn_factory):
    rec = TaskRecorder(conn_factory)
    a = rec.open("k1", "invoicing", "Invoice FLV-42026 for Fattoria La Vino")
    rec.set_status(a.id, "waiting")
    rec.open("k2", "invoicing", "Receipt for Metro")
    body = client.get("/v1/tasks").json()
    assert {t["id"] for t in body["items"]} == {a.id, "SC-101"}
    assert body["counts"] == {"waiting": 1, "in_progress": 1}
    assert [t["id"] for t in client.get("/v1/tasks?status=waiting").json()["items"]] == [a.id]
    assert [t["id"] for t in client.get("/v1/tasks?q=fattoria").json()["items"]] == [a.id]


def test_task_detail_has_steps_in_order(client, conn_factory):
    rec = TaskRecorder(conn_factory)
    t = rec.open("k", "invoicing", "x")
    for i in range(3):
        rec.step(t.id, "note", f"s{i}")
    d = client.get(f"/v1/tasks/{t.id}").json()
    assert [s["text"] for s in d["steps"]] == ["s0", "s1", "s2"] and d["approval"] is None
    assert client.get("/v1/tasks/SC-999").status_code == 404


def test_approve_requires_token(client, conn_factory, db):
    t = TaskRecorder(conn_factory).open("k", "liquidity", "oven")
    db.execute("INSERT INTO approvals (id, task_id, approver_id, question) VALUES ('apr_x', %s, 'vincenzo', 'q')",
               (t.id,))
    db.commit()
    assert client.post("/v1/approvals/apr_x:approve").status_code == 401
    r = client.post("/v1/approvals/apr_x:approve", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    job = db.execute("SELECT kind, payload FROM agent_jobs").fetchone()
    assert job["kind"] == "approval" and job["payload"]["by"] == "vincenzo"


def test_oauth_state_tamper_rejected(client):
    url = client.get("/v1/connections/google/start", headers=AUTH).json()["url"]
    assert url.startswith("https://accounts.google.com/") and "gmail.readonly" in url
    state = scout_api.make_state(SECRET)
    bad = state[:-2] + ("AA" if not state.endswith("AA") else "BB")
    r = client.get(f"/v1/connections/google/callback?code=c&state={bad}", follow_redirects=False)
    assert r.status_code == 400
    expired = scout_api.make_state(SECRET, now=0)
    assert not scout_api.check_state(SECRET, expired)


def test_oauth_callback_stores_encrypted(client, db, monkeypatch):
    db.execute("INSERT INTO connections (id, kind, provider, display_name) VALUES ('gmail','mail','gmail','Email')")
    db.commit()
    id_token = "h." + __import__("base64").urlsafe_b64encode(b'{"email":"office@amalfi.test"}').decode().rstrip("=") + ".s"
    monkeypatch.setattr(scout_api, "exchange_code",
                        lambda code, s: {"refresh_token": "1//refresh-secret", "id_token": id_token})
    r = client.get(f"/v1/connections/google/callback?code=c&state={scout_api.make_state(SECRET)}",
                   follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://dash.test/connections?google=connected"
    ct = bytes(db.execute("SELECT ciphertext FROM connection_credentials").fetchone()["ciphertext"])
    assert b"refresh-secret" not in ct and open_(ct, client.key) == "1//refresh-secret"
    row = db.execute("SELECT status, config FROM connections WHERE id='gmail'").fetchone()
    assert row["status"] == "connected" and row["config"]["email"] == "office@amalfi.test"
    conns = client.get("/v1/connections").json()["items"]
    assert conns[0]["has_credentials"] is True and "ciphertext" not in conns[0]


def test_demo_disabled_404(client):
    assert client.post("/v1/demo/play/files", headers=AUTH).status_code == 404


def test_audit_filters(client, conn_factory):
    from scout.audit import Audit

    a = Audit(conn_factory)
    a.record("agent", "read", "files:grazyna", "Downloads/x.pdf", task_id="SC-1")
    a.record("agent", "access_check", "laptop:vincenzo", "folders", "DENY", requester="vanessa")
    assert len(client.get("/v1/audit").json()["items"]) == 2
    assert [i["requester"] for i in client.get("/v1/audit?decision=deny").json()["items"]] == ["vanessa"]
