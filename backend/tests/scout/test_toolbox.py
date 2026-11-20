"""Scout's DM tools: access is checked for the person asking; the loop survives a looping small model."""

from __future__ import annotations

from cfo.llm import FakeLLM
from scout.loops import chat
from scout.loops.toolbox import SPECS, Toolbox
from test_invoicing_loop import make_world, pay, run_txn, texts
from test_other_loops import dm


def box(ctx, who: str):  # noqa: ANN001, ANN201
    t = ctx.recorder.open(f"dm:test:{who}", "chat", "q")
    return Toolbox(ctx, who, ctx.hr.role(who), t)


def test_specs_are_small_model_friendly():
    assert len(SPECS) == 14
    for s in SPECS:
        assert len(s.description) < 200
        assert all(p.get("type") in ("string", "integer", "boolean") for p in s.parameters["properties"].values())


def test_files_respect_the_asker(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    found = box(ctx, "grazyna").call("search_files", {"person": "Grazyna", "words": "AS-77012"})
    assert found["files"] == ["Downloads/Invoice_AS-77012.pdf"]
    text = box(ctx, "grazyna").call("read_file", {"person": "grazyna", "path": "Downloads/Invoice_AS-77012.pdf"})
    assert "Total (EUR) 1286.40" in text["text"]
    assert box(ctx, "vanessa").call("search_files", {"person": "Grazyna", "words": "invoice"})["error"] == "access_denied"
    assert box(ctx, "grazyna").call("search_files", {"person": "Vincenzo", "words": "x"})["error"] == "access_denied"
    private = box(ctx, "grazyna").call("read_file", {"person": "grazyna", "path": "Private/personal-notes.txt"})
    assert private["error"] in ("not_found_or_private", "access_denied")


def test_money_tools(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    pay(ctx, "nowhere")
    b = box(ctx, "vincenzo")
    rows = b.call("search_payments", {"undocumented_only": True})["rows"]
    assert [r["counterparty"] for r in rows] == ["Fattoria La Vino GmbH"]
    assert b.call("cash_position", {})["money_out_30d"] == "€869.62"
    asked = run_txn(ctx, pay(ctx, "nowhere", txn_id="tx_flv2"))
    miss = b.call("missing_invoices", {})["payments_without_invoice"]
    assert {(m["counterparty"], m["amount"]) for m in miss} == {("Fattoria La Vino GmbH", "€869.62")}
    assert {m["scout_task"] for m in miss} == {"none yet", asked.id} and "Vincenzo" in {m["asked"] for m in miss}
    done = run_txn(ctx, pay(ctx, "files", txn_id="tx_files2"))
    log = b.call("task_log", {"task_id": done.id.lower()})
    assert log["task"]["status"] == "done" and any("Filed invoice AS-77012" in s for s in log["steps"])
    assert b.call("who_is", {"person": "Grazyna"})["people"][0]["on_vacation_today"] is True
    assert b.call("nope", {})["error"] == "unknown_tool"
    assert b.call("task_log", {})["error"] == "bad_arguments"


def test_what_if_tool_owner_only(conn_factory, db, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    assert box(ctx, "rosa").call("start_what_if", {"amount_eur": "30000"})["error"] == "access_denied"
    assert box(ctx, "vincenzo").call("start_what_if", {"month": "March"}) == {"error": "amount_missing"}
    assert box(ctx, "vincenzo").call("start_what_if", {"amount_eur": "30000", "month": "March", "what": "oven"})["started"]
    assert db.execute("SELECT count(*) AS n FROM agent_jobs WHERE kind='what_if'").fetchone()["n"] == 1


def test_multi_step_answer_and_loop_guard(conn_factory, tmp_path):
    calls = []

    def handler(messages, tools):  # noqa: ANN001
        names = {t.name for t in tools or []}
        if "route_message" in names:
            return FakeLLM.tool_call("route_message", intent="question")
        n_tool = sum(m.role == "tool" for m in messages)
        calls.append(n_tool)
        if not tools:
            return "Final: €869.62 to Fattoria La Vino has no invoice yet; Scout asked Vincenzo."
        if n_tool == 0:
            return FakeLLM.tool_call("search_payments", undocumented_only=True)
        if n_tool == 1:
            return FakeLLM.tool_call("scout_tasks", text="Fattoria")
        return FakeLLM.tool_call("scout_tasks", text="Fattoria")  # loops forever on the same call

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    run_txn(ctx, pay(ctx, "nowhere"))
    t = chat.run(ctx, dm("vincenzo", "which payments still miss an invoice?"))
    steps = texts(ctx, t.id)
    assert steps[1] == "Looked at search payments(undocumented_only=True): 1 result(s)"
    assert steps[2] == "Looked at scout tasks(text=Fattoria): 1 result(s)"
    assert sum("scout tasks" in s for s in steps) == 1  # the repeated call was not run again
    assert ctx.notifier.updates[-1]["text"].startswith("Final: €869.62")
    assert len(calls) == chat.MAX_ROUNDS + 1  # bounded: 8 rounds, then one answer without tools


def test_repair_broken_small_model_json():
    from scout.loops.toolbox import repair_args

    raw = '{\n  "counterparty": "Fattoria La Vino",\n  "days": 365,\n  "undocu'
    assert repair_args({"_raw": raw}) == {"counterparty": "Fattoria La Vino", "days": 365}
    assert repair_args({"_raw": '{"undocumented_only": true}'}) == {"undocumented_only": True}
    assert repair_args({"a": 1}) == {"a": 1} and repair_args(None) == {}


def test_slack_markdown():
    assert chat.slack_markdown("You have **€31,016.31**\n### Summary") == "You have *€31,016.31*\n*Summary*"
