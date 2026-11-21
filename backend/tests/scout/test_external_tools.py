"""External chat models (OpenAI, Mistral, Groq, xAI) get a small tool allowlist and no company data. The control is
`Toolbox.call()`, not the list of specs the model is shown."""

from __future__ import annotations

import json

from api_test_helpers import TestClient
from fastapi import FastAPI

from cfo.api.errors import install_error_handlers
from cfo.api.settings import get_api_settings
from cfo.llm import FakeLLM
from scout import chat_api
from scout.config import get_scout_settings
from scout.loops import chat
from scout.loops.toolbox import EXTERNAL_TOOLS, SPECS, Toolbox
from test_invoicing_loop import make_world, pay, run_txn

BRIEFING = "CEO daily briefing · 2026-11-21\nSales target: below target. Revenue 41,230.55 EUR.\nNext steps: hire."


def open_task(ctx, who: str = "vincenzo"):  # noqa: ANN001, ANN201
    return ctx.recorder.open(f"web:ext:{who}", "chat", "q", requested_by=who, owner_id=who)


def external_box(ctx, who: str = "vincenzo"):  # noqa: ANN001, ANN201
    return Toolbox(ctx, who, ctx.hr.role(who), open_task(ctx, who), allowed=EXTERNAL_TOOLS)


def add_briefing(ctx, text: str = BRIEFING, day: str = "2026-11-21"):  # noqa: ANN001, ANN201
    t = ctx.recorder.open(f"strategy:{day}", "strategy", f"CEO daily briefing · {day}", owner_id="vincenzo",
                          category="strategy", level="AUTO")
    return ctx.recorder.set_status(t.id, "done", outcome=text, resolved_via="auto", level="AUTO")


def link_slack(ctx) -> None:  # noqa: ANN001
    ctx.hr.link_slack({"vincenzo": "U_VIN", "grazyna": "U_GRA"}, {"vincenzo": "Wojciech Fiołka", "grazyna": "Grażyna"})


def test_allowlist_is_the_four_safe_tools():
    assert EXTERNAL_TOOLS == {"dashboard_help", "start_what_if", "send_dm", "send_briefing"}
    assert EXTERNAL_TOOLS <= {s.name for s in SPECS}


def test_forged_calls_are_refused_and_touch_nothing(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    pay(ctx, "nowhere")
    ext = run_txn(ctx, pay(ctx, "nowhere", txn_id="tx_flv2"))  # a task that close_tasks could close
    box = external_box(ctx)
    with conn_factory() as conn:
        audited = conn.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"]
    for name, args in [("search_payments", {}), ("cash_position", {}), ("search_invoices", {}), ("read_file", {"person": "grazyna", "path": "x"}),
                       ("search_email", {}), ("search_chat", {"supplier": "x"}), ("send_invoices", {}),
                       ("close_tasks", {"task_ids": ext.id, "reason": "forged by the model"}),
                       ("hand_over_task", {"task_id": ext.id, "person": "grazyna"})]:
        out = box.call(name, args)
        assert out["error"] == "not_available_for_this_model", name
    with conn_factory() as conn:
        assert conn.execute("SELECT count(*) AS n FROM audit_log").fetchone()["n"] == audited  # no ALLOW row for anything
    assert ctx.recorder.get(ext.id).status != "done"  # close_tasks did not run
    assert box.call("nope", {})["error"] == "unknown_tool"
    assert set(box.call("nope", {})["tools"]) == EXTERNAL_TOOLS  # the model is not told about the others


def test_without_an_allowlist_every_tool_runs(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    pay(ctx, "nowhere")
    box = Toolbox(ctx, "vincenzo", "ceo", open_task(ctx))
    assert box.call("cash_position", {})["money_out_30d"] == "€869.62"


def test_allowlisted_dm_works_and_help_works(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    link_slack(ctx)
    box = external_box(ctx, "grazyna")
    out = box.call("send_dm", {"person": "Wojtek", "text": "The Asimex invoice is filed."})
    assert out["sent"] is True
    assert ctx.notifier.sent[-1]["text"] == "Message from Grazyna via Scout: The Asimex invoice is filed."
    assert "error" not in box.call("dashboard_help", {})
    assert box.call("start_what_if", {"amount_eur": "30000"})["error"] == "access_denied"  # owner-only still applies


def test_send_briefing_sends_and_returns_no_text(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    link_slack(ctx)
    add_briefing(ctx)
    out = external_box(ctx).call("send_briefing", {"person": "Grazyna"})
    assert out == {"sent": True, "to": "Grażyna", "title": "CEO daily briefing · 2026-11-21", "date": "2026-11-21"}
    assert "41,230" not in json.dumps(out)
    msg = ctx.notifier.sent[-1]
    assert msg["to"] == "grazyna" and "41,230.55" in msg["text"]
    assert msg["text"].rstrip().endswith("from Vincenzo via Scout")
    with conn_factory() as conn:
        row = conn.execute("SELECT * FROM audit_log WHERE action = 'send_briefing'").fetchone()
    assert row["requester"] == "vincenzo" and row["target"] == "slack:grazyna"


def test_send_briefing_picks_the_newest_finished_one_from_either_writer(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    link_slack(ctx)
    add_briefing(ctx, "old text", "2026-11-19")
    news = ctx.recorder.open("strategy-news:abc", "strategy", "Daily strategy news review · 2026-11-21 08:00",
                             category="strategy", level="AUTO")
    ctx.recorder.set_status(news.id, "done", outcome="Fresh news summary", resolved_via="auto", level="AUTO")
    unfinished = ctx.recorder.open("strategy:2026-11-22", "strategy", "CEO daily briefing · 2026-11-22", category="strategy")
    assert unfinished.status != "done"
    out = external_box(ctx).call("send_briefing", {"person": "Grazyna"})
    assert out["title"].startswith("Daily strategy news review") and "Fresh news summary" in ctx.notifier.sent[-1]["text"]


def test_send_briefing_is_exec_only_and_reports_problems(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    link_slack(ctx)
    for who in ("grazyna", "rosa"):
        out = external_box(ctx, who).call("send_briefing", {"person": "Wojtek"})
        assert out["error"] == "access_denied" and not ctx.notifier.sent
    box = external_box(ctx)
    assert box.call("send_briefing", {"person": "Grazyna"})["error"] == "no_briefing"  # nothing finished yet
    add_briefing(ctx)
    assert box.call("send_briefing", {})["error"] == "missing_arguments"
    assert box.call("send_briefing", {"person": "Norbert"})["error"] == "unknown_person"
    assert box.call("send_briefing", {"person": "Rosa"})["error"] == "not_on_slack"
    assert not ctx.notifier.sent


def test_send_briefing_splits_what_slack_accepts_and_says_so(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    link_slack(ctx)
    add_briefing(ctx, "CEO daily briefing\n" + "\n".join(f"Line {i}: " + "x" * 200 for i in range(200)))
    out = external_box(ctx).call("send_briefing", {"person": "Grazyna"})
    assert out["sent"] is True and out["truncated"] is True and set(out) == {"sent", "to", "title", "date", "truncated"}
    parts = [m for m in ctx.notifier.sent if m["to"] == "grazyna"]
    assert 1 < len(parts) <= 3 and all(len(m["text"]) <= 2800 for m in parts)
    assert "from Vincenzo via Scout" in parts[-1]["text"]


def run_chat(ctx, _unused, allowed, text="hi", **kw):  # noqa: ANN001, ANN201
    task = open_task(ctx)
    return chat.answer(ctx, task, text, "vincenzo", "ceo", allowed_tools=allowed, **kw)


def test_external_answer_sees_only_allowlisted_specs_and_a_restricted_prompt(conn_factory, tmp_path):
    seen: list[tuple[set[str], str]] = []

    def handler(messages, tools):  # noqa: ANN001
        seen.append(({t.name for t in tools or []}, messages[0].content))
        if not any(m.role == "tool" for m in messages):
            return FakeLLM.tool_call("search_payments", days=30)  # forged: not in its list
        return "I cannot see company data; pick Granite."

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    pay(ctx, "nowhere")
    reply = run_chat(ctx, None, EXTERNAL_TOOLS, "what is our bank balance")
    assert reply == "I cannot see company data; pick Granite."
    assert seen[0][0] == EXTERNAL_TOOLS
    system = seen[0][1]
    assert "no access to company data" in system.lower() and "Granite" in system
    assert "send_briefing" in system and "send_dm" in system and "start_what_if" in system
    tool_msgs = [s for s in ctx.recorder.steps(open_task(ctx).id)]
    assert not any("€869.62" in s.text for s in tool_msgs)


def test_external_answer_never_runs_the_invoice_fallback(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=lambda m, t: "Sorry, I cannot do that."))
    reply = run_chat(ctx, None, EXTERNAL_TOOLS, "send me the invoices as PDF")
    assert reply == "Sorry, I cannot do that."
    assert not ctx.notifier.files


def test_granite_answer_gets_every_spec(conn_factory, tmp_path):
    seen: list[set[str]] = []

    def handler(messages, tools):  # noqa: ANN001
        seen.append({t.name for t in tools or []})
        return "Granite answer."

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    assert run_chat(ctx, None, None) == "Granite answer."
    assert seen[0] == {s.name for s in SPECS}
    system = ctx.llm.calls[0]["messages"][0].content
    assert "no access to company data" not in system.lower()


def test_compacting_an_external_history_only_summarizes_what_it_was_given(conn_factory, tmp_path):
    summarized: list[str] = []

    def handler(messages, tools):  # noqa: ANN001
        if tools is None and "Summarize" in messages[0].content:
            summarized.append(messages[1].content)
            return "Summary of public talk."
        return "Fine."

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    history = []
    for i in range(12):
        history += [{"role": "user", "content": f"{i}: " + "public " * 1500}, {"role": "assistant", "content": "answer " * 1000}]
    run_chat(ctx, None, EXTERNAL_TOOLS, "and now?", history=history)
    assert summarized and "public" in summarized[0] and "€" not in summarized[0]


def test_dashboard_endpoint_restricts_external_but_not_granite(conn_factory, tmp_path, monkeypatch):
    granite_tools: list[set[str]] = []
    external_tools: list[set[str]] = []
    granite = FakeLLM(handler=lambda m, t: (granite_tools.append({x.name for x in t or []}), "Granite.")[1])
    gpt = FakeLLM(handler=lambda m, t: (external_tools.append({x.name for x in t or []}), "Grok.")[1])
    monkeypatch.setattr(chat_api.ExternalModel, "llm", lambda self: gpt)
    ctx = make_world(conn_factory, tmp_path, llm=granite)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.get_context_factory] = lambda: (lambda s: ctx)
    app.dependency_overrides[get_scout_settings] = lambda: ctx.settings
    monkeypatch.setenv("API_WRITE_TOKEN", "tok")
    get_api_settings.cache_clear()
    c = TestClient(app)
    headers = {"Authorization": "Bearer tok"}
    msgs = {"messages": [{"role": "user", "content": "hello"}]}
    assert c.post("/v1/chat", json=msgs, headers=headers).status_code == 200
    ext = {"provider": "xai", "model": "grok-4.7", "api_key": "xai-test-123456"}
    assert c.post("/v1/chat", json={**msgs, "external": ext}, headers=headers).status_code == 200
    assert granite_tools[0] == {s.name for s in SPECS}
    assert external_tools[0] == EXTERNAL_TOOLS
    get_api_settings.cache_clear()
