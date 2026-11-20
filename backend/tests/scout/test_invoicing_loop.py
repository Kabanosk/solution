"""Invoicing loop: the evidence ladder writes the deck's step lines, stops at the first rung that explains the
payment, asks a person (vacation-aware) otherwise, then reminds and escalates."""

from __future__ import annotations

import base64
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from cfo.data import people
from cfo.db.repo import Repository
from cfo.domain import Invoice, Transaction, Vacation
from cfo.llm import FakeLLM
from scout.audit import Audit
from scout.config import ScoutSettings
from scout.connectors.chat import NoChat, SlackChat
from scout.connectors.files import WorkstationFiles
from scout.connectors.invoicing import PostgresInvoicing
from scout.connectors.mail import FolderMail, UnavailableMail
from scout.connectors.registry import Connectors
from scout.demo_data import SCENARIOS
from scout.jobs import JobQueue
from scout.loops import invoicing
from scout.loops.context import HR, JobContext, LogNotifier
from scout.models import BankTxn, Job
from scout.seed import plant_demo_evidence
from scout.tasks import TaskRecorder

NOW = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def fake_llm(messages, tools):  # noqa: ANN001
    """Reads the real PDF text for extraction; classifiers answer from keywords."""
    names = {t.name for t in tools or []}
    user = messages[-1].content or ""
    if "submit_invoice" in names:
        number = re.search(r"INVOICE (\S+)", user)
        total = re.search(r"Total \(EUR\) ([\d.,]+)", user)
        if not (number and total):
            return "I cannot read this."
        return FakeLLM.tool_call("submit_invoice", number=number.group(1), supplier=user.split("\n")[2].strip(),
                                 issue_date="2026-11-15", total=total.group(1), lines=[])
    if "classify_message" in names:
        if "refund" in user:
            return FakeLLM.tool_call("classify_message", label="refund_expected", refund_amount="1250.00",
                                     refund_by="2026-11-28", reason="deposit refund")
        return FakeLLM.tool_call("classify_message", label="unrelated")
    if "classify_reply" in names:
        if "later" in user:
            return FakeLLM.tool_call("classify_reply", label="promises_later")
        return FakeLLM.tool_call("classify_reply", label="explains", category="Team events")
    return "Without the invoice the expense is a tax risk."


@pytest.fixture
def world(conn_factory, tmp_path):  # noqa: ANN001
    return make_world(conn_factory, tmp_path)


def make_world(conn_factory, tmp_path, *, grazyna_away: bool = True, llm=None, mail=True, slack=None):  # noqa: ANN001
    conn = conn_factory()
    repo = Repository(conn)
    with repo.transaction():
        for team in people.make_teams({}):
            repo.add_team(team)
        for e in people.EMPLOYEES:
            vac = [Vacation(start=TODAY - timedelta(days=2), end=TODAY + timedelta(days=10))] \
                if (e.id == "grazyna" and grazyna_away) else []
            repo.add_employee(e.model_copy(update={"vacations": vac}))
    conn.close()
    plant_demo_evidence(tmp_path, TODAY)
    audit = Audit(conn_factory)
    settings = ScoutSettings(database_url="postgresql://unused@localhost/x", reminder_after_hours=24)
    connectors = Connectors(
        bank=None,
        invoicing=PostgresInvoicing(conn_factory, audit),
        files=WorkstationFiles(tmp_path / "workstations", audit),
        mail=FolderMail(tmp_path / "mailbox", audit) if mail else UnavailableMail(),
        chat=SlackChat(slack, ["finance"], audit) if slack else NoChat(),
    )
    clock = [NOW]
    ctx = JobContext(conn_factory=conn_factory, recorder=TaskRecorder(conn_factory), audit=audit,
                     connectors=connectors, llm=llm or FakeLLM(handler=fake_llm), notifier=LogNotifier(),
                     jobs=JobQueue(conn_factory), settings=settings, hr=HR(conn_factory), clock=lambda: clock[0])
    ctx.test_clock = clock  # type: ignore[attr-defined]
    ctx.data_dir = tmp_path  # type: ignore[attr-defined]
    return ctx


def pay(ctx: JobContext, key: str, *, txn_id: str | None = None) -> BankTxn:
    s = SCENARIOS[key]
    txn = BankTxn(id=txn_id or f"tx_{key}", booked_at=NOW - timedelta(minutes=5), amount=s.amount,
                  counterparty=s.counterparty, reference=s.reference, type=s.type, card_last4=s.card_last4,
                  holder_employee_id=s.holder_employee_id)
    conn = ctx.conn_factory()
    repo = Repository(conn)
    with repo.transaction():
        repo.add_transaction(Transaction(id=txn.id, booking_date=TODAY, type=txn.type, reference=txn.reference,
                                         counterparty=txn.counterparty, amount=txn.amount,
                                         invoice_number=s.invoice_number))
    conn.close()
    return txn


def job(kind: str, payload: dict, n: int = 1) -> Job:
    return Job(id=n, kind=kind, payload=payload, dedupe_key=f"{kind}:{n}", status="running", attempts=1,
               run_after=NOW)


def run_txn(ctx: JobContext, txn: BankTxn):  # noqa: ANN201
    return invoicing.run(ctx, job("bank_txn", {"txn": txn.model_dump(mode="json")}))


def kinds(ctx: JobContext, task_id: str) -> list[str]:
    return [s.kind for s in ctx.recorder.steps(task_id)]


def texts(ctx: JobContext, task_id: str) -> list[str]:
    return [s.text for s in ctx.recorder.steps(task_id)]


def test_found_in_invoicing_skips_rest(world):
    txn = pay(world, "files")
    conn = world.conn_factory()
    Repository(conn).add_invoice(Invoice(id="inv_as77012", number="AS-77012", supplier="Asimex Wholesale Ltd",
                                         issue_date=TODAY, total=Decimal("1286.40")))
    conn.close()
    task = run_txn(world, txn)
    assert task.status == "done" and task.resolved_via == "invoicing"
    assert kinds(world, task.id)[:2] == ["picked_up", "checked_system"]
    assert "checked_files" not in kinds(world, task.id)
    assert texts(world, task.id)[0] == "Scout picked up the transfer from Revolut Business"
    assert texts(world, task.id)[1] == "Searched the invoicing system: 1 match"


def test_found_in_files(world):
    task = run_txn(world, pay(world, "files"))
    assert task.status == "done" and task.resolved_via == "files"
    assert task.outcome == "Resolved · €1,286.40 filed as AS-77012"
    k = kinds(world, task.id)
    assert k[:3] == ["picked_up", "checked_system", "checked_files"] and k[-1] == "resolved"
    assert "Checked Grazyna's company folders: found Invoice_AS-77012.pdf" in texts(world, task.id)
    assert any(e["kind"] == "pdf" for e in task.evidence)
    with world.conn_factory() as conn:
        assert conn.execute("SELECT documented FROM transactions WHERE id='tx_files'").fetchone()["documented"]
        denied = conn.execute("SELECT count(*) AS n FROM audit_log WHERE resource LIKE '%%Private%%'").fetchone()["n"]
    assert denied == 0  # never touched the private folder
    assert world.notifier.sent[0]["to"] == "grazyna" and "FYI" in world.notifier.sent[0]["text"]


def test_found_in_email(world):
    task = run_txn(world, pay(world, "email"))
    assert task.status == "done" and task.resolved_via == "email", texts(world, task.id)
    assert "Checked company email: found" in " ".join(texts(world, task.id))
    assert "checked_chat" not in kinds(world, task.id)


def test_chat_refund_adds_expected_cash_and_followup(conn_factory, tmp_path, fake_slack):
    s = SCENARIOS["chat_refund"]
    fake_slack.history["C_FIN"] = [{"ts": f"{NOW.timestamp() - 3600:.6f}", "user": "U_GRAZ", "text": s.chat_message}]
    ctx = make_world(conn_factory, tmp_path, slack=fake_slack)
    task = run_txn(ctx, pay(ctx, "chat_refund"))
    assert task.status == "done" and task.resolved_via == "chat_context", texts(ctx, task.id)
    assert "Checked authorized chat history: found context in #finance" in texts(ctx, task.id)
    with conn_factory() as conn:
        ev = conn.execute("SELECT * FROM expected_cash_events").fetchall()
        follow = conn.execute("SELECT * FROM agent_tasks WHERE dedupe_key = %s", (f"followup:{task.id}",)).fetchone()
    assert len(ev) == 1 and ev[0]["amount"] == Decimal("1250.00") and ev[0]["on_date"] == date(2026, 11, 28)
    assert follow["status"] == "todo" and follow["title"].startswith("Watch Pasta Fresca")


def test_nothing_found_asks_owner_then_waiting(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    task = run_txn(ctx, pay(ctx, "nowhere"))
    assert task.status == "waiting" and task.owner_id == "grazyna"
    assert kinds(ctx, task.id) == ["picked_up", "checked_system", "checked_files", "checked_email", "checked_chat",
                                   "asked_person"]
    assert texts(ctx, task.id)[-1] == "Asked the document owner, Grazyna, on Slack"
    dm = ctx.notifier.sent[-1]
    assert dm["to"] == "grazyna" and "€869.62" in dm["text"] and "FLV-42026" in dm["text"]
    assert task.slack_ref == "D_grazyna:1.0"
    with conn_factory() as conn:
        rem = conn.execute("SELECT * FROM agent_jobs WHERE kind='reminder'").fetchall()
    assert len(rem) == 1 and rem[0]["run_after"] == NOW + timedelta(hours=24)


def test_vacation_goes_to_manager(world):
    task = run_txn(world, pay(world, "nowhere"))
    assert task.owner_id == "vincenzo"
    assert texts(world, task.id)[-1] == "Grazyna is on vacation, asked Vincenzo on Slack"


def test_reply_with_pdf_resolves(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    task = run_txn(ctx, pay(ctx, "nowhere"))
    pdf = (tmp_path / "demo" / "FLV-42026.pdf").read_bytes()
    out = invoicing.run(ctx, job("reply", {"task_id": task.id, "from_id": "grazyna", "text": "",
                                           "files": [{"name": "FLV-42026.pdf",
                                                      "data_b64": base64.b64encode(pdf).decode()}]}, 2))
    assert out.status == "done" and out.resolved_via == "slack_dm"
    assert "Grazyna sent FLV-42026.pdf" in texts(ctx, task.id)
    assert ctx.notifier.sent[-1]["text"].startswith("Thanks! I filed €869.62 as FLV-42026")


def test_reply_text_explains_and_promise(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    task = run_txn(ctx, pay(ctx, "nowhere"))
    t = invoicing.run(ctx, job("reply", {"task_id": task.id, "from_id": "grazyna", "text": "I'll send it later"}, 2))
    assert t.status == "waiting"
    t = invoicing.run(ctx, job("reply", {"task_id": task.id, "from_id": "grazyna", "text": "wine for the offsite"}, 3))
    assert t.status == "done" and t.category == "Team events"


def test_reminder_then_escalation_then_ceo(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, grazyna_away=False)
    task = run_txn(ctx, pay(ctx, "nowhere"))
    t = invoicing.run(ctx, job("reminder", {"task_id": task.id}, 2))
    assert t.status == "waiting" and texts(ctx, task.id)[-1] == "Reminded Grazyna"
    t = invoicing.run(ctx, job("reminder", {"task_id": task.id}, 3))
    assert t.status == "escalated" and t.owner_id == "vincenzo"
    assert texts(ctx, task.id)[-1] == "Escalated to Vincenzo"
    t = invoicing.run(ctx, job("reminder", {"task_id": task.id}, 4))
    assert texts(ctx, task.id)[-1] == "Reminded Vincenzo"
    t = invoicing.run(ctx, job("reminder", {"task_id": task.id}, 5))
    assert t.status == "escalated" and "left for Vincenzo" in texts(ctx, task.id)[-1]


def test_same_txn_twice_one_task(world):
    txn = pay(world, "nowhere")
    a = run_txn(world, txn)
    b = run_txn(world, txn)
    assert a.id == b.id
    assert kinds(world, a.id).count("picked_up") == 1
    assert len(world.notifier.sent) == 1


def test_llm_garbage_still_finishes(conn_factory, tmp_path):
    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=lambda m, t: "banana"))
    task = run_txn(ctx, pay(ctx, "files"))
    assert task.status == "waiting"  # could not read the PDF: falls through to asking a person
    assert kinds(ctx, task.id)[-1] == "asked_person"


def test_connector_failure_is_an_error_step(world):
    class Broken:
        name = "eml_folder"

        def available(self):
            return True

        def search(self, *a, **k):
            raise ConnectionError("imap down")

    world.connectors.mail = Broken()
    task = run_txn(world, pay(world, "nowhere"))
    assert "error" in kinds(world, task.id) and task.status == "waiting"
