"""Cost control, liquidity (approval interrupt) and the chat loop."""

from __future__ import annotations

import base64
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from cfo.db.repo import Repository
from cfo.domain import CostMetric, CostMetricKind
from cfo.llm import FakeLLM
from scout.loops import chat, cost_control, liquidity
from scout.models import Job
from test_invoicing_loop import NOW, TODAY, fake_llm, job, kinds, make_world, pay, run_txn, texts


# -- cost control -----------------------------------------------------------------------------------------
def test_cloud_50pct_one_task_notify(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    conn = conn_factory()
    repo = Repository(conn)
    with repo.transaction():
        for i, (cost, orders) in enumerate([("400", "1000"), ("410", "1010"), ("615", "1040")]):
            m = date(2026, 8 + i, 1)
            repo.add_cost_metric(CostMetric(kind=CostMetricKind.CLOUD_PER_ORDER, month=m, team_id="it",
                                            cost=Decimal(cost), metric_value=Decimal(orders)))
    conn.close()
    tasks = cost_control.run(ctx, job("cost_check", {}))
    assert len(tasks) == 1
    t = tasks[0]
    assert t.status == "done" and t.level == "NOTIFY" and t.loop == "cost_control"
    assert "Compared cloud cost for the ordering app with online orders: +50% vs +3%" in texts(ctx, t.id)
    assert texts(ctx, t.id)[-1] == "Alerted Vincenzo"
    assert cost_control.run(ctx, job("cost_check", {}, 2)) == []  # never twice


# -- liquidity --------------------------------------------------------------------------------------------
def fake_review(gap: bool):  # noqa: ANN201
    def forecaster(ctx, outflows):  # noqa: ANN001
        days = pd.date_range(TODAY, periods=200, freq="D", name="date")
        low = -12000.0 if gap else 25000.0
        fc = pd.DataFrame({"balance": [40000.0] * 100 + [low] * 100}, index=days)
        gaps, proposals = [], []
        if gap:
            gaps = [SimpleNamespace(shortfall=Decimal("22000"), lowest_date=date(2027, 3, 2))]
            proposals = [SimpleNamespace(instrument="credit line", amount=Decimal("25000"), action_type="request_credit_line",
                                         period_start=date(2027, 3, 1), period_end=date(2027, 4, 30), month="2027-03",
                                         reason="Buying the oven in March leaves €12,000 below the buffer.")]
        return SimpleNamespace(forecast=fc, buffer=Decimal("10000"), gaps=gaps, proposals=proposals)
    return forecaster


def test_what_if_oven_creates_approval_then_approve_simulated(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.forecaster = fake_review(gap=True)
    t = liquidity.run(ctx, job("what_if", {"amount": "30000", "month": "March", "what": "an oven",
                                           "from_id": "vincenzo"}))
    assert t.status == "waiting" and t.level == "APPROVAL" and t.approval_id
    assert kinds(ctx, t.id)[-2:] == ["proposed", "asked_approval"]
    msg = ctx.notifier.sent[-1]
    assert msg["to"] == "vincenzo" and [b.action_id for b in msg["buttons"]] == ["scout_approve", "scout_reject"]
    out = liquidity.resume(ctx, t.approval_id, True, "vincenzo")
    assert kinds(ctx, t.id)[-2:] == ["approved", "resolved"]
    assert out.status == "done" and "SIMULATED" in out.outcome
    assert "Approved by Vincenzo" in ctx.notifier.updates[-1]["text"]
    assert liquidity.resume(ctx, t.approval_id, False, "vincenzo").status == "done"  # second click: no change


def test_only_approver_can_click(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.forecaster = fake_review(gap=True)
    t = liquidity.run(ctx, job("what_if", {"amount": "30k", "month": "2027-03", "what": "oven", "from_id": "vincenzo"}))
    with pytest.raises(PermissionError):
        liquidity.resume(ctx, t.approval_id, True, "vanessa")
    with conn_factory() as conn:
        deny = conn.execute("SELECT * FROM audit_log WHERE decision='DENY'").fetchall()
    assert len(deny) == 1
    assert liquidity.resume(ctx, t.approval_id, False, "vincenzo").status == "cancelled"


def test_what_if_affordable_is_done(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.forecaster = fake_review(gap=False)
    t = liquidity.run(ctx, job("what_if", {"amount": "3000", "month": "December", "what": "a mixer",
                                           "from_id": "vincenzo"}))
    assert t.status == "done" and t.outcome.startswith("Resolved · affordable")


def test_parse_month():
    assert liquidity.parse_month("March", date(2026, 11, 20)) == date(2027, 3, 1)
    assert liquidity.parse_month("2027-05", date(2026, 11, 20)) == date(2027, 5, 1)
    assert liquidity.parse_month("december", date(2026, 11, 20)) == date(2026, 12, 1)
    # small models hand over stale years ("2024-03" from "in march"): the next such month, never the past
    assert liquidity.parse_month("2024-03", date(2026, 11, 20)) == date(2027, 3, 1)
    assert liquidity.parse_month("march 2025", date(2026, 11, 20)) == date(2027, 3, 1)
    assert liquidity.parse_month("2026-11", date(2026, 11, 20)) == date(2026, 11, 1)
    assert liquidity.parse_eur("50k$") == Decimal(50000) and liquidity.parse_eur("$50,000 USD") == Decimal(50000)
    assert liquidity.parse_eur("30k") == Decimal(30000) and liquidity.parse_eur("€1,250.50") == Decimal("1250.50")


# -- chat -------------------------------------------------------------------------------------------------
def dm(from_id: str, text: str = "", files=None, n: int = 10, **extra) -> Job:  # noqa: ANN001
    return job("dm", {"from_id": from_id, "text": text, "files": files or [], "status_ref": f"D_{from_id}:s{n}",
                      **extra}, n)


def waiting_task(ctx):  # noqa: ANN001, ANN201
    t = run_txn(ctx, pay(ctx, "nowhere"))
    assert t.status == "waiting" and t.owner_id == "grazyna"
    return t


def b64(path) -> str:  # noqa: ANN001
    return base64.b64encode(path.read_bytes()).decode()


def test_reply_pdf_resolves_waiting_task(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    t = waiting_task(ctx)
    out = chat.run(ctx, dm("grazyna", files=[{"name": "FLV-42026.pdf", "data_b64": b64(tmp_path / "demo" / "FLV-42026.pdf")}]))
    assert out.id == t.id and out.status == "done" and out.resolved_via == "slack_dm"
    assert ctx.notifier.tracked[t.id] == "D_grazyna:s10"
    assert ctx.notifier.updates[-1]["text"].startswith("Resolved · €869.62 filed as FLV-42026")


def test_reply_pdf_mismatch_keeps_waiting(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    t = waiting_task(ctx)
    other = tmp_path / "workstations" / "grazyna" / "Downloads" / "Invoice_AS-77012.pdf"
    out = chat.run(ctx, dm("grazyna", files=[{"name": "Invoice_AS-77012.pdf", "data_b64": b64(other)}]))
    assert out.id == t.id and out.status == "waiting"
    assert "I couldn't read an invoice for this payment" in texts(ctx, t.id)[-1] or \
        "The PDF shows" in texts(ctx, t.id)[-1]


def test_reply_non_pdf_attachment(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    t = waiting_task(ctx)
    out = chat.run(ctx, dm("grazyna", files=[{"name": "photo.jpg", "data_b64": base64.b64encode(b"\xff\xd8jpg").decode()}]))
    assert out.status == "waiting"
    assert texts(ctx, t.id)[-1] == "Received a file that is not a PDF"


def route_llm(intent: str, **extra):  # noqa: ANN201
    def handler(messages, tools):  # noqa: ANN001
        names = {t.name for t in tools or []}
        if "route_message" in names:
            return FakeLLM.tool_call("route_message", intent=intent, **extra)
        return fake_llm(messages, tools)
    return FakeLLM(handler=handler)


def test_vanessa_request_ceo_laptop_denied(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, llm=route_llm("request_files", target_person="Vincenzo"))
    out = chat.run(ctx, dm("vanessa", "send me the contract from Vincenzo's laptop"))
    assert out.loop == "chat" and out.status == "done"
    assert "denied" in kinds(ctx, out.id)
    assert "can't" in ctx.notifier.updates[-1]["text"]
    with conn_factory() as conn:
        deny = conn.execute("SELECT * FROM audit_log WHERE decision='DENY' AND target='laptop:vincenzo'").fetchall()
    assert len(deny) == 1 and deny[0]["requester"] == "vanessa"


def test_what_if_dm_enqueues_job(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, llm=route_llm("what_if", amount_eur="30000", month="March", what="oven"))
    chat.run(ctx, dm("vincenzo", "what if I buy an oven for 30k in March?"))
    with conn_factory() as conn:
        j = conn.execute("SELECT * FROM agent_jobs WHERE kind='what_if'").fetchone()
    assert j["payload"]["amount"] == "30000" and j["payload"]["from_id"] == "vincenzo"


def test_question_from_waitstaff_denied_ceo_answered(conn_factory, tmp_path):
    def handler(messages, tools):  # noqa: ANN001
        names = {t.name for t in tools or []}
        if "route_message" in names:
            return FakeLLM.tool_call("route_message", intent="question")
        if "search_payments" in names and not any(m.role == "tool" for m in messages):
            return FakeLLM.tool_call("search_payments", counterparty="Fattoria")
        return "You paid Fattoria La Vino €869.62 today; Scout is waiting for the invoice."

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    pay(ctx, "nowhere")
    denied = chat.run(ctx, dm("vanessa", "what did we pay Fattoria?", n=1))
    assert texts(ctx, denied.id)[1] == "Tried search payments(counterparty=Fattoria): access denied"
    with conn_factory() as conn:
        deny = conn.execute("SELECT * FROM audit_log WHERE decision='DENY' AND target='bank'").fetchall()
    assert len(deny) == 1 and deny[0]["requester"] == "vanessa"
    ok = chat.run(ctx, dm("vincenzo", "what did we pay Fattoria?", n=2))
    assert "looked_up" in kinds(ctx, ok.id) and ctx.notifier.updates[-1]["text"].startswith("You paid Fattoria")


def test_smalltalk_help(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, llm=route_llm("smalltalk"))
    chat.run(ctx, dm("rosa", "hi"))
    assert ctx.notifier.updates[-1]["text"].startswith("I'm Scout")


def test_requester_hears_the_approval(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.forecaster = fake_review(gap=True)
    t = liquidity.run(ctx, job("what_if", {"amount": "30000", "month": "March", "what": "an oven",
                                           "from_id": "grazyna", "status_ref": "D_gra:s1"}))
    assert ctx.notifier.updates[-1]["ref"] == "D_gra:s1" and "Waiting for" in ctx.notifier.updates[-1]["text"]
    liquidity.resume(ctx, t.approval_id, True, "vincenzo")
    [mine] = [u for u in ctx.notifier.updates if u["ref"] == "D_gra:s1"][-1:]
    assert mine["text"].startswith("Resolved · ") and "Vincenzo approved" in mine["text"] and t.id in mine["text"]
    assert mine["level"] == "NOTIFY"


def test_requester_without_status_message_gets_a_dm(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.forecaster = fake_review(gap=True)
    t = liquidity.run(ctx, job("what_if", {"amount": "30000", "month": "March", "what": "an oven",
                                           "from_id": "grazyna"}))
    liquidity.resume(ctx, t.approval_id, False, "vincenzo")
    last = ctx.notifier.sent[-1]
    assert last["to"] == "grazyna" and "rejected" in last["text"] and last["task_id"] == t.id


def test_what_if_from_chat_reports_back_into_the_chat(conn_factory, tmp_path):
    from scout.loops.toolbox import Toolbox

    ctx = make_world(conn_factory, tmp_path)
    ctx.forecaster = fake_review(gap=True)
    chat_task = ctx.recorder.open("web:conv12345", "chat", "Dashboard chat")
    out = Toolbox(ctx, "vincenzo", "ceo", chat_task).call("start_what_if", {"amount_eur": "30000", "month": "March",
                                                                            "what": "an oven"})
    assert out["started"] and "this chat" in out["note"]
    with conn_factory() as conn:
        j = conn.execute("SELECT * FROM agent_jobs WHERE kind='what_if'").fetchone()
    assert j["payload"]["chat_task"] == chat_task.id
    t = liquidity.run(ctx, job("what_if", j["payload"]))
    follow = [s for s in ctx.recorder.steps(chat_task.id) if s.kind == "followup"]
    assert len(follow) == 1 and "approval" in follow[0].text.lower() and t.id in follow[0].text
    liquidity.resume(ctx, t.approval_id, True, "vincenzo")
    follow = [s for s in ctx.recorder.steps(chat_task.id) if s.kind == "followup"]
    assert len(follow) == 2 and "approved" in follow[1].text and "SIMULATED" in follow[1].text
    assert follow[1].detail["task"] == t.id


def test_a_slack_thread_is_one_conversation_with_history(conn_factory, tmp_path):
    seen = []

    def handler(messages, tools):  # noqa: ANN001
        names = {t.name for t in tools or []}
        if "route_message" in names:
            return FakeLLM.tool_call("route_message", intent="question")
        seen.append([m.content for m in messages if m.role in ("user", "assistant")])
        return f"Answer {len(seen)}."

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    first = chat.run(ctx, dm("vincenzo", "which tasks need me?", n=1, thread="D1:9.0"))
    second = chat.run(ctx, dm("vincenzo", "mark them done", n=2, thread="D1:9.0"))
    assert first.id == second.id and first.loop == "chat"
    assert seen[1][:3] == ["which tasks need me?", "Answer 1.", "mark them done"]


def test_file_requests_need_a_file_word(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, llm=route_llm("request_files"))
    out = chat.run(ctx, dm("vincenzo", "no i mean make them approved and done"))
    assert "checked_files" not in kinds(ctx, out.id)


def test_approval_handed_to_someone_else_who_approves_by_typing(conn_factory, tmp_path):
    from scout.loops.toolbox import Toolbox

    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    ctx.hr.link_slack({"vincenzo": "U_VIN", "grazyna": "U_GRA"}, {})
    ctx.forecaster = fake_review(gap=True)
    chat_task = ctx.recorder.open("web:conv77777777", "chat", "Dashboard chat")
    t = liquidity.run(ctx, job("what_if", {"amount": "30000", "month": "March", "what": "an oven",
                                           "from_id": "vincenzo", "chat_task": chat_task.id}))
    # only the current approver (or the CEO) may hand it over
    denied = Toolbox(ctx, "vanessa", "staff", chat_task).call("reassign_approval", {"task_id": t.id, "person": "Rosa"})
    assert denied["error"] == "access_denied"
    out = Toolbox(ctx, "vincenzo", "ceo", chat_task).call("reassign_approval",
                                                         {"task_id": t.id, "person": "Grazyna"})
    assert out["reassigned"] is True and out["to"] == "Grazyna"
    card = ctx.notifier.sent[-1]
    assert card["to"] == "grazyna" and [b.action_id for b in card["buttons"]] == ["scout_approve", "scout_reject"]
    task = ctx.recorder.get(t.id)
    assert task.owner_id == "grazyna" and task.status == "waiting"
    assert any("Grazyna" in s.text for s in ctx.recorder.steps(chat_task.id) if s.kind == "followup")
    with pytest.raises(PermissionError):  # the old approver no longer decides
        liquidity.resume(ctx, task.approval_id, True, "vincenzo")
    # Grazyna types "I approve" in the card's thread
    chat.run(ctx, dm("grazyna", "I approve", n=31, thread=task.slack_ref))
    done = ctx.recorder.get(t.id)
    assert done.status == "done" and "SIMULATED" in done.outcome
    assert any("Grazyna approved" in s.text for s in ctx.recorder.steps(chat_task.id) if s.kind == "followup")


def test_unclear_typed_answer_to_an_approval_asks_for_the_buttons(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path)
    ctx.forecaster = fake_review(gap=True)
    t = liquidity.run(ctx, job("what_if", {"amount": "30000", "month": "March", "what": "oven", "from_id": "vincenzo"}))
    chat.run(ctx, dm("vincenzo", "hmm what does this mean", n=32, thread=ctx.recorder.get(t.id).slack_ref))
    assert ctx.recorder.get(t.id).status == "waiting"
    assert "Approve" in ctx.notifier.updates[-1]["text"] or "Approve" in ctx.notifier.sent[-1]["text"]
