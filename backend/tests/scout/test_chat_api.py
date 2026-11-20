from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cfo.api.errors import install_error_handlers
from cfo.api.settings import get_api_settings
from cfo.llm import FakeLLM
from scout import chat_api
from scout.config import get_scout_settings
from test_invoicing_loop import make_world, pay, run_txn


def test_chat_streams_steps_then_answer(conn_factory, tmp_path, monkeypatch):
    seen_history = []

    def handler(messages, tools):  # noqa: ANN001
        if not any(m.role == "tool" for m in messages):
            seen_history.append([m.role for m in messages])
            return FakeLLM.tool_call("missing_invoices", days=30)
        return "One payment has no invoice: **€869.62** to Fattoria La Vino."

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    run_txn(ctx, pay(ctx, "nowhere"))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.get_context_factory] = lambda: (lambda s: ctx)
    app.dependency_overrides[get_scout_settings] = lambda: ctx.settings
    monkeypatch.setenv("API_WRITE_TOKEN", "tok")
    get_api_settings.cache_clear()
    c = TestClient(app)
    body = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"},
                         {"role": "user", "content": "Which payments miss an invoice?"}]}
    assert c.post("/v1/chat", json=body).status_code == 401
    r = c.post("/v1/chat", json=body, headers={"Authorization": "Bearer tok"})
    events = [json.loads(line) for line in r.text.splitlines()]
    assert [e["type"] for e in events] == ["step", "delta", "done"]
    assert events[0]["text"] == "Looked at missing invoices(days=30): 1 result(s)"
    assert events[1]["text"].startswith("One payment has no invoice: **€869.62**")
    task = ctx.recorder.get(events[2]["task_id"])
    assert task.loop == "chat" and task.status == "done" and task.requested_by == "vincenzo"
    assert seen_history[0] == ["system", "user", "assistant", "user"]
    get_api_settings.cache_clear()


def test_chat_streams_a_sent_file(conn_factory, tmp_path, monkeypatch):
    def handler(messages, tools):  # noqa: ANN001
        if not any(m.role == "tool" for m in messages):
            return FakeLLM.tool_call("send_invoices", supplier="Asimex", days=3650)
        return "Sent the Asimex invoices to your Slack DM."

    from datetime import date
    from decimal import Decimal

    from cfo.db.repo import Repository
    from cfo.domain import Invoice

    conn = conn_factory()
    Repository(conn).add_invoice(Invoice(id="inv_as77012", number="AS-77012", supplier="Asimex Wholesale Ltd",
                                         issue_date=date(2026, 11, 2), total=Decimal("1286.40")))
    conn.close()
    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.get_context_factory] = lambda: (lambda s: ctx)
    app.dependency_overrides[get_scout_settings] = lambda: ctx.settings
    monkeypatch.setenv("API_WRITE_TOKEN", "tok")
    get_api_settings.cache_clear()
    r = TestClient(app).post("/v1/chat", json={"messages": [{"role": "user", "content": "send me the invoices"}]},
                             headers={"Authorization": "Bearer tok"})
    events = [json.loads(line) for line in r.text.splitlines()]
    assert [e["type"] for e in events] == ["step", "file", "delta", "done"]
    f = events[1]
    assert f["name"] == "Invoice_AS-77012.pdf" and f["mime"] == "application/pdf" and f["sent_to_slack"] is True
    assert f["data_b64"]
    get_api_settings.cache_clear()
