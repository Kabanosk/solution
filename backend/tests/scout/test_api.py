from __future__ import annotations

import pytest
from fastapi import FastAPI
from api_test_helpers import TestClient

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
    assert client.post("/v1/approvals/apr_x:approve", headers={"Authorization": ""}).status_code == 401
    r = client.post("/v1/approvals/apr_x:approve", headers=AUTH)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    job = db.execute("SELECT kind, payload FROM agent_jobs").fetchone()
    assert job["kind"] == "approval" and job["payload"]["by"] == "vincenzo"


def test_oauth_state_tamper_rejected(client):
    assert client.get("/v1/connections/google/start", headers=AUTH).status_code == 403
    state = scout_api.make_state(SECRET)
    bad = state[:-2] + ("AA" if not state.endswith("AA") else "BB")
    r = client.get(f"/v1/connections/google/callback?code=c&state={bad}", follow_redirects=False)
    assert r.status_code == 403
    assert not scout_api.check_state(SECRET, bad)
    expired = scout_api.make_state(SECRET, now=0)
    assert not scout_api.check_state(SECRET, expired)


def test_disabled_oauth_callback_cannot_store_credentials(client, db, monkeypatch):
    db.execute("INSERT INTO connections (id, kind, provider, display_name) VALUES ('gmail','mail','gmail','Email')")
    db.commit()
    id_token = "h." + __import__("base64").urlsafe_b64encode(b'{"email":"office@amalfi.test"}').decode().rstrip("=") + ".s"
    monkeypatch.setattr(scout_api, "exchange_code",
                        lambda code, s: {"refresh_token": "1//refresh-secret", "id_token": id_token})
    r = client.get(f"/v1/connections/google/callback?code=c&state={scout_api.make_state(SECRET)}",
                   follow_redirects=False)
    assert r.status_code == 403
    assert db.execute("SELECT ciphertext FROM connection_credentials").fetchone() is None
    row = db.execute("SELECT status, config FROM connections WHERE id='gmail'").fetchone()
    assert row["status"] == "disconnected" and row["config"] == {}
    conns = client.get("/v1/connections").json()["items"]
    assert conns[0]["has_credentials"] is False and "ciphertext" not in conns[0]


def test_demo_mutations_are_disabled(client):
    assert client.post("/v1/demo/play/files", headers=AUTH).status_code == 403


def test_audit_filters(client, conn_factory):
    from scout.audit import Audit

    a = Audit(conn_factory)
    a.record("agent", "read", "files:grazyna", "Downloads/x.pdf", task_id="SC-1")
    a.record("agent", "access_check", "laptop:vincenzo", "folders", "DENY", requester="vanessa")
    assert len(client.get("/v1/audit").json()["items"]) == 2
    assert [i["requester"] for i in client.get("/v1/audit?decision=deny").json()["items"]] == ["vanessa"]


def _book_with_invoice(db) -> None:  # noqa: ANN001
    db.execute("INSERT INTO invoices (id, number, supplier, issue_date, total, transaction_id)"
               " VALUES ('inv_flv1', 'FLV-1', 'Fattoria La Vino', '2026-10-01', 120.00, 'tx1')")
    db.execute("INSERT INTO invoice_lines (invoice_id, position, description, quantity, unit_price, amount)"
               " VALUES ('inv_flv1', 0, 'Chianti 0.75l', 10, 12, 120.00)")
    db.execute("INSERT INTO transactions (id, booking_date, type, reference, counterparty, amount, invoice_number,"
               " documented, invoice_id) VALUES ('tx1', '2026-10-02', 'card', 'FLV-1', 'Fattoria La Vino', -120.00,"
               " 'FLV-1', TRUE, 'inv_flv1'), ('tx2', '2026-10-03', 'card', 'x', 'Metro', -5.00, NULL, FALSE, NULL)")
    db.commit()


def test_transaction_invoice_with_document(client, conn_factory, db):
    _book_with_invoice(db)
    rec = TaskRecorder(conn_factory)
    task = rec.open("k-inv", "invoicing", "Invoice for Fattoria La Vino", transaction_id="tx1")
    rec.step(task.id, "acted", "Filed invoice FLV-1", source="invoicing_pg",
             detail={"invoice_id": "inv_flv1", "lines": 1, "text_excerpt": "INVOICE FLV-1 Total 120.00"},
             evidence={"name": "FLV-1.pdf", "kind": "pdf"})
    body = client.get("/v1/transactions/tx1/invoice").json()
    assert body["invoice"]["number"] == "FLV-1" and body["invoice"]["total"] == "120.00"
    assert body["invoice"]["issue_date"] == "2026-10-01"
    assert [(line["description"], line["amount"]) for line in body["lines"]] == [("Chianti 0.75l", "120.00")]
    assert body["transaction"]["amount"] == "-120.00"
    assert body["document"]["text_excerpt"] == "INVOICE FLV-1 Total 120.00"
    assert body["document"]["evidence"]["name"] == "FLV-1.pdf"
    assert body["task_id"] == task.id == body["document"]["task_id"]


def test_transaction_invoice_without_document_and_missing(client, db):
    _book_with_invoice(db)
    body = client.get("/v1/transactions/tx1/invoice").json()
    assert body["document"] is None and body["task_id"] is None
    assert client.get("/v1/transactions/tx2/invoice").json()["error"]["code"] == "no_invoice"
    assert client.get("/v1/transactions/nope/invoice").status_code == 404
