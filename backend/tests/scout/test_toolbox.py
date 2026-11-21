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
    assert len(SPECS) == 19
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


def test_empty_arguments_use_the_question(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    run_txn(ctx, pay(ctx, "files"))
    t = ctx.recorder.open("dm:q", "chat", "q")
    b = Toolbox(ctx, "vincenzo", "ceo", t, question="Did we get the Asimex invoice AS-77012?")
    rows = b.call("search_invoices", {})["rows"]
    assert [(r["number"], r["linked_to_payment"]) for r in rows] == [("AS-77012", "True")]
    assert Toolbox(ctx, "vincenzo", "ceo", t, question="hi").call("search_invoices", {})["error"] == "missing_arguments"


def test_email_without_words_lists_the_newest_mails(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    b = box(ctx, "vincenzo")
    newest = b.call("search_email", {"days": 365})
    assert newest["mails"] and "hint" not in newest
    dates = [m["date"] for m in newest["mails"] if m["date"]]
    assert dates == sorted(dates, reverse=True)
    nothing = b.call("search_email", {"words": "topic", "days": 365})
    assert nothing["mails"] == [] and "empty words" in nothing["hint"]


def test_send_invoices_attaches_the_pdfs(conn_factory, tmp_path):
    import base64
    from datetime import date
    from decimal import Decimal

    from cfo.db.repo import Repository
    from cfo.domain import Invoice, InvoiceLine

    conn = conn_factory()
    repo = Repository(conn)
    repo.add_invoice(Invoice(id="inv_as77012", number="AS-77012", supplier="Asimex Wholesale Ltd",
                             issue_date=date(2026, 11, 2), total=Decimal("1286.40")))
    repo.add_invoice(Invoice(id="inv_cld", number="CLD-2026-11", supplier="Nimbus Cloud EU",
                             issue_date=date(2026, 11, 3), total=Decimal("366.02"),
                             lines=[InvoiceLine(description="Cloud hosting", quantity=Decimal(1),
                                                unit_price=Decimal("366.02"), amount=Decimal("366.02"))]))
    conn.close()
    ctx = make_world(conn_factory, tmp_path)
    b = box(ctx, "vincenzo")
    out = b.call("send_invoices", {"days": 3650})
    assert out["sent_to_slack"] is True and out["invoices"] == 2
    assert {f["file"]: f["source"] for f in out["files"]} == {
        "Invoice_AS-77012.pdf": "original from Grazyna's company folders",
        "Invoice_CLD-2026-11.pdf": "copy from the invoicing system"}
    [up] = ctx.notifier.files
    assert up["to"] == "vincenzo" and [n for n, _ in up["files"]] == ["Invoice_CLD-2026-11.pdf", "Invoice_AS-77012.pdf"]
    assert all(data[:5] == b"%PDF-" for _, data in up["files"])
    sent = [s for s in ctx.recorder.steps(b.task.id) if s.kind == "sent_file"]
    assert [s.detail["name"] for s in sent] == ["Invoice_CLD-2026-11.pdf", "Invoice_AS-77012.pdf"]
    assert sent[0].detail["mime"] == "application/pdf" and base64.b64decode(sent[0].detail["data_b64"])[:5] == b"%PDF-"
    assert b.call("send_invoices", {"number": "AS-77012"})["invoices"] == 1
    assert box(ctx, "vanessa").call("send_invoices", {})["error"] == "access_denied"


def test_send_dm_messages_a_colleague_on_slack_with_attribution(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.hr.link_slack({"vincenzo": "U_VIN"}, {"vincenzo": "Wojciech Fiołka"})
    b = box(ctx, "grazyna")
    out = b.call("send_dm", {"person": "Wojtek", "text": "The Asimex invoice is filed."})
    assert out["sent"] is True and out["to"] == "Wojciech Fiołka" and out["awaiting_reply"] is True
    msg = ctx.notifier.sent[-1]
    assert msg["to"] == "vincenzo" and msg["text"] == "Message from Grazyna via Scout: The Asimex invoice is filed."
    with conn_factory() as conn:
        row = conn.execute("SELECT * FROM audit_log WHERE action = 'send_dm'").fetchone()
    assert row["requester"] == "grazyna" and row["target"] == "slack:vincenzo"
    assert b.call("send_dm", {"person": "Rosa", "text": "hi"})["error"] == "not_on_slack"
    assert b.call("send_dm", {"person": "Norbert", "text": "hi"})["error"] == "unknown_person"
    assert b.call("send_dm", {"person": "Wojtek", "text": " "})["error"] == "missing_arguments"


def test_close_tasks_by_their_owner_with_a_reason(conn_factory, tmp_path):
    from test_other_loops import waiting_task

    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    t = waiting_task(ctx)
    assert box(ctx, "grazyna").call("close_tasks", {"task_ids": t.id})["error"] == "missing_arguments"
    denied = box(ctx, "vanessa").call("close_tasks", {"task_ids": t.id, "reason": "refund, no invoice"})
    assert denied["closed"] == [] and denied["skipped"][0]["why"] == "not yours to close"
    out = box(ctx, "grazyna").call("close_tasks", {"task_ids": "all", "reason": "It was refunded, no invoice"})
    assert out["closed"] == [t.id]
    done = ctx.recorder.get(t.id)
    assert done.status == "done" and done.resolved_via == "slack_dm" and "refunded" in done.outcome
    assert any(u["ref"] == done.slack_ref and "Closed" in u["text"] for u in ctx.notifier.updates)
    again = box(ctx, "grazyna").call("close_tasks", {"task_ids": t.id.lower(), "reason": "x refund"})
    assert again["closed"] == [] and again["skipped"][0]["why"] == "already done"
