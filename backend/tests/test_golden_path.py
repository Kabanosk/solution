"""Golden path end to end (T011): demo-flow steps 1-7 through the real wiring of `cfo.run`.

Real parts: generated data (history seed + December replay), the simulator, the LangGraph agent, the MCP tool
objects (in-process), the policy engine, the forecast model, the chat router, the REST API. Fakes: the LLM
(`FakeLLM`, reads the real PDF text) and the chat channel (`FakeChannel`, a `cfo.channels.base.Notifier` that
records DMs and lets the test click buttons and type messages). Everything lives in the isolated Postgres schema
`t011_test`, dropped afterwards. Skips when Postgres or `data/generated` is missing.

The tests are the steps of `.house/demo-flow.md` in the order the generated data plays them (the FLV payment
of step 3 is booked on 29 Dec, the AS payment of step 2 on 30 Dec) and share one world, so they run in file order.
Step 5 follows D-013: the scheduled forecast review proposes an investment (the balance stays above the safety
buffer), the credit line comes from the CEO's what-if ("oven for 30,000 EUR in March").
"""

from __future__ import annotations

import os
import re
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from cfo.agent.tools import InProcessToolBox
from cfo.api import deps as api_deps
from cfo.api import services
from cfo.api.app import create_app
from cfo.channels.base import (
    SIMULATED,
    ActionKind,
    ApprovalAnswer,
    ChatMessage,
    ChatReply,
    Notifier,
)
from cfo.config import REPO_ROOT, get_settings
from cfo.data.load import load_events, seed
from cfo.db.migrate import SCHEMA_PATH
from cfo.db.repo import Repository
from cfo.domain import ApprovalStatus, DecisionStatus, EventKind, PolicyLevel
from cfo.llm import FakeLLM
from cfo.mcp_servers.common import make_repo_factory
from cfo.mcp_servers.company_db import CompanyDbTools
from cfo.mcp_servers.hr import HrTools
from cfo.mcp_servers.workstations import DbAccessLog, WorkstationTools
from cfo.sim.engine import Simulator
from fastapi.testclient import TestClient

SCHEMA = f"t011_test_{os.getpid()}"
CEO, OFFICE = "vincenzo", "grazyna"
MISSING_TXN, MISSING_INV = "txn_1116", "FLV-42026"  # paid 29 Dec, no document anywhere
FOUND_TXN, FOUND_INV, FOUND_FILE = "txn_1120", "AS-69664", "Downloads/AS-69664.pdf"  # paid 30 Dec


# -- fakes ------------------------------------------------------------------------------------------------
class FakeChannel:
    """`cfo.channels.base.Notifier` without Slack: records DMs, `click` / `say` play the people."""

    def __init__(self):
        self.sent: list[dict] = []
        self.started = False
        self._approval = None
        self._chat = None

    def send_dm(self, employee, text, level, actions=()):
        self.sent.append({"to": employee.id, "text": text, "level": str(level), "actions": list(actions)})
        return f"fake:{len(self.sent)}"

    def on_approval(self, handler):
        self._approval = handler

    def on_chat(self, handler):
        self._chat = handler

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def to(self, employee_id):
        return [m for m in self.sent if m["to"] == employee_id]

    def click(self, request_id, approved, employee_id):
        self._approval(ApprovalAnswer(request_id=request_id, approved=approved, employee_id=employee_id,
                                      message_ref="fake:click"))

    def say(self, employee_id, text) -> ChatReply | None:
        out = self._chat(ChatMessage(employee_id=employee_id, text=text, message_ref="fake:chat"))
        return ChatReply(text=out) if isinstance(out, str) else out


LINE = re.compile(r"^(?P<d>.+?) (?P<q>\d+) (?P<u>\d+\.\d{2}) (?P<a>\d+\.\d{2})$")


def fake_llm(messages, tools):
    """Plays the model: reads invoices from the document text, routes the two chat sentences, explains."""
    names = {t.name for t in tools or []}
    user = messages[-1].content or ""
    if "submit_invoice" in names:
        number = re.search(r"INVOICE (\S+)", user)
        total = re.search(r"Total \(EUR\) ([\d.]+)", user)
        if not (number and total):
            return "I cannot read this document."
        lines = [{"description": m["d"], "quantity": m["q"], "unit_price": m["u"], "amount": m["a"]}
                 for m in map(LINE.match, user.splitlines()) if m]
        return FakeLLM.tool_call("submit_invoice", number=number.group(1), supplier=re.search(r"^.+ (?:Ltd|GmbH)$", user, re.M).group(0),
                                 issue_date="2025-12-21", total=total.group(1), lines=lines)
    if "search_queries" in names:
        return FakeLLM.tool_call("search_queries", queries=["Fattoria", "Rechnung"])
    if "plan_purchase" in names and "oven" in user:
        return FakeLLM.tool_call("plan_purchase", what="oven", amount_eur="30000", month="2026-03")
    if "create_rule" in names and "sprzęt" in user:
        return FakeLLM.tool_call("create_rule", level="APPROVAL", category="equipment")
    if "create_rule" in names:
        return "Hello! I can take a rule or a what-if question."
    return "Plain-language explanation of the facts."


class World:
    pass


def _generated_dir() -> Path | None:
    for parent in [REPO_ROOT, *REPO_ROOT.parents]:
        candidate = parent / "data" / "generated"
        if (candidate / "bank" / "history.jsonl").is_file():
            return candidate
    return None


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from cfo.run import Worker, build_runtime

    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    data = _generated_dir()
    if data is None:
        pytest.skip("generated data missing (python -m cfo.data.generate)")

    mp = pytest.MonkeyPatch()
    mp.setenv("FORECAST_MODEL_PATH", str(tmp_path_factory.mktemp("model") / "liquidity_xgb.json"))
    services._model_cache.clear()

    admin = Repository.connect(url)
    admin.conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    admin.conn.execute(f"CREATE SCHEMA {SCHEMA}")
    admin.conn.execute(f"SET search_path TO {SCHEMA}")
    admin.conn.commit()
    admin.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    admin.conn.commit()
    seed(admin, data)

    w = World()
    w.factory = make_repo_factory(url, schema=SCHEMA)
    w.channel = FakeChannel()
    assert isinstance(w.channel, Notifier)
    w.llm = FakeLLM(handler=fake_llm)
    tools = InProcessToolBox(CompanyDbTools(w.factory), WorkstationTools(data / "workstations", DbAccessLog(w.factory)),
                             HrTools(w.factory))
    w.runtime = build_runtime(repo_factory=w.factory, tools=tools, llm=w.llm, channel=w.channel)
    w.now = [1_000_000.0]
    w.sim = Simulator(w.factory(), load_events(data), speed=3600.0, clock=lambda: w.now[0],
                      subscribers=[w.runtime.on_event])
    w.worker = Worker(w.runtime, w.sim)

    app = create_app()

    def repo_override():
        with w.factory() as repo:
            yield repo

    app.dependency_overrides[api_deps.get_repo] = repo_override
    w.api = TestClient(app)
    w.approvals = {}  # action_type -> approval id, filled by the steps

    yield w

    api_deps.set_agent(None)
    services._model_cache.clear()
    mp.undo()
    w.sim.repo.conn.close()
    admin.conn.rollback()
    admin.conn.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    admin.conn.commit()
    admin.conn.close()


def decisions(w, action_type=None, txn=None):
    """Oldest first."""
    with w.factory() as r:
        out = list(reversed(r.list_decisions(limit=500)))
    return [d for d in out if (action_type is None or d.action_type == action_type)
            and (txn is None or d.payload.get("transaction_id") == txn)]


def advance(w, to: datetime):
    """Play the replay up to `to` (inclusive) and let the worker do what became due."""
    w.sim.jump(to)
    w.worker.pump()


def api_decisions(w, **params):
    r = w.api.get("/v1/decisions", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


# -- step 1: the dashboard has history --------------------------------------------------------------------
def test_step1_dashboard_shows_history(world):
    w = world
    kpis = w.api.get("/v1/kpis").json()
    assert kpis["stub"] is False and kpis["as_of"] == "2025-11-30" and kpis["pending_approvals"] == 0
    forecast = w.api.get("/v1/forecast/liquidity", params={"days": 90}).json()
    assert forecast["points"] and Decimal(forecast["safety_buffer"]["amount"]) > 0
    assert {t["team_id"] for t in w.api.get("/v1/costs/by-team").json()["items"]} >= {"kitchen", "it"}
    assert {s["kind"] for s in w.api.get("/v1/cost-metrics").json()["items"]} == {
        "food_cost", "ads_cac", "cloud_per_order"}
    assert api_decisions(w) == [] and w.channel.sent == []


def test_the_worker_registers_itself_with_the_channel_and_the_api(world):
    w = world
    assert api_deps.get_agent() is w.runtime.agent  # POST /v1/approvals/{id}:approve reaches this agent
    assert w.channel._approval is not None and w.channel._chat is not None


# -- December starts: routine days need nobody ------------------------------------------------------------
def test_routine_december_days_produce_no_decisions(world):
    w = world
    advance(w, datetime(2025, 12, 29, 0, 0, tzinfo=UTC))
    assert w.sim.status().emitted > 80
    assert decisions(w) == [] and w.channel.sent == []
    with w.factory() as r:
        december = [t for t in r.list_transactions(undocumented_only=True) if t.booking_date.month == 12]
    assert december == []  # payments whose invoice is in the database are documented on arrival


# -- step 3: no document anywhere, Grazyna on vacation -> the CEO, with the reason ------------------------
def test_step3_missing_invoice_goes_to_ceo_because_grazyna_is_on_vacation(world):
    w = world
    advance(w, datetime(2025, 12, 29, 8, 0, tzinfo=UTC))
    (dec,) = decisions(w, txn=MISSING_TXN)
    assert dec.action_type == "request_invoice" and dec.level == PolicyLevel.NOTIFY
    assert dec.payload["recipient"] == CEO and dec.payload["skipped_on_vacation"] == [OFFICE]
    (dm,) = w.channel.to(CEO)
    assert dm["level"] == "NOTIFY" and MISSING_INV in dm["text"]
    assert "Belegpflicht" in dm["text"] and "on vacation" in dm["text"]
    # D-004: the owner of the folders is told that the agent looked, and the access is in the log
    (notice,) = w.channel.to(OFFICE)
    assert "company folders" in notice["text"] and MISSING_INV in notice["text"]
    with w.factory() as r:
        assert not r.get_transaction(MISSING_TXN).documented
        assert r._all("SELECT count(*) AS n FROM access_log WHERE employee = %s", (OFFICE,))[0]["n"] > 0


# -- step 2: document found on Grazyna's computer -> AUTO --------------------------------------------------
def test_step2_invoice_found_in_downloads_is_attached_on_its_own(world):
    w = world
    before = len(w.channel.to(OFFICE))
    advance(w, datetime(2025, 12, 30, 8, 0, tzinfo=UTC))
    (dec,) = decisions(w, "attach_invoice")
    assert dec.level == PolicyLevel.AUTO and dec.status == DecisionStatus.EXECUTED and dec.actor == "agent"
    assert dec.payload["transaction_id"] == FOUND_TXN and dec.payload["path"] == FOUND_FILE
    with w.factory() as r:
        txn = r.get_transaction(FOUND_TXN)
        inv = r.get_invoice_by_number(FOUND_INV)
    assert txn.documented and txn.invoice_id == inv.id
    assert inv.total == Decimal("623.48") == -txn.amount and inv.source == FOUND_FILE
    assert len(inv.lines) == 6 and sum(ln.amount for ln in inv.lines) == inv.total
    assert inv.supplier == "Asimex Wholesale Ltd"
    (dm,) = w.channel.to(OFFICE)[before:]
    assert dm["level"] == "AUTO" and FOUND_INV in dm["text"] and FOUND_FILE in dm["text"]
    (item,) = api_decisions(w, action_type="attach_invoice")
    assert item["level"] == "AUTO"


def test_step3_reminder_fires_after_24_simulated_hours(world):
    w = world  # the request of 29 Dec 08:00 was due for a reminder at 30 Dec 08:00, which `advance` just passed
    kinds = [d.action_type for d in decisions(w, txn=MISSING_TXN)]
    assert kinds == ["request_invoice", "remind_invoice"]
    assert "Reminder" in w.channel.to(CEO)[-1]["text"]
    w.worker.pump()  # nothing is due twice
    assert len(decisions(w, txn=MISSING_TXN)) == 2


# -- step 4: cloud cost +50%, online orders +3% -> NOTIFY --------------------------------------------------
def test_step4_cloud_cost_anomaly_alerts_the_ceo(world):
    w = world
    before = len(w.channel.to(CEO))
    w.sim.jump(datetime(2025, 12, 31, 8, 0, tzinfo=UTC))  # the cloud bill arrives; no `pump` yet
    (dec,) = decisions(w, "alert_cost_anomaly")
    assert dec.level == PolicyLevel.NOTIFY and dec.payload["kind"] == "cloud_per_order"
    facts = dec.payload["facts"]
    assert 49 < facts["cost_change_pct"] < 51 and 2 < facts["metric_change_pct"] < 4
    assert "Log ingestion" in facts["biggest_increase"]["line"]
    assert facts["cost_per_unit_now"] and facts["cost_per_unit_change_pct"] > 40  # cloud cost per online order
    (alert,) = w.channel.to(CEO)[before:]  # one DM: the CEO also owns the IT team
    assert alert["level"] == "NOTIFY" and alert["text"] == dec.summary  # the explanation (worded by the LLM)
    assert w.api.get("/v1/kpis").json()["open_cost_alerts"] == 1


# -- step 5 (D-013 a): the scheduled forecast review -> investment proposal, APPROVAL ----------------------
def test_step5_scheduled_forecast_review_proposes_and_waits_for_approval(world):
    w = world
    advance(w, datetime(2025, 12, 31, 9, 0, tzinfo=UTC))  # the replay is over: the month is closed
    assert w.sim.status().finished
    (dec,) = decisions(w, "make_investment")
    assert dec.level == PolicyLevel.APPROVAL and dec.status == DecisionStatus.PENDING and not dec.executed_simulated
    assert decisions(w, "request_credit_line") == []  # D-013: the real forecast stays above the buffer
    dm = w.channel.to(CEO)[-1]
    assert dm["level"] == "APPROVAL" and "safety buffer" in dm["text"] and "June 2026" in dm["text"]
    assert [a.kind for a in dm["actions"]] == [ActionKind.APPROVE, ActionKind.REJECT]
    assert {a.request_id for a in dm["actions"]} == {dec.approval_request_id}
    w.approvals["make_investment"] = dec.approval_request_id
    # the checks are deduplicated: another round changes nothing
    w.runtime.run_checks(w.sim.status().sim_time)
    assert len(decisions(w, "make_investment")) == 1 and len(decisions(w, "alert_cost_anomaly")) == 1
    (pending,) = w.api.get("/v1/approvals", params={"state": "pending"}).json()["items"]
    assert pending["id"] == dec.approval_request_id
    assert w.api.get("/v1/kpis").json()["pending_approvals"] == 1


# -- step 5 (D-013 b): the CEO's what-if breaks the buffer -> credit line request, APPROVAL ----------------
def test_step5_ceo_what_if_leads_to_a_credit_line_request(world):
    w = world
    assert "CEO" in w.channel.say(OFFICE, "What if I buy a new oven for 30000 EUR in March?").text
    assert decisions(w, "request_credit_line") == []  # only the CEO may ask the agent for scenarios
    reply = w.channel.say(CEO, "What if I buy a new oven for 30000 EUR in March?")
    (dec,) = decisions(w, "request_credit_line")
    assert dec.level == PolicyLevel.APPROVAL and dec.status == DecisionStatus.PENDING
    assert "credit line" in reply.text and "30,000" in reply.text
    dm = w.channel.to(CEO)[-1]
    assert dm["level"] == "APPROVAL" and "safety buffer" in dm["text"] and "oven" in dm["text"]
    assert [a.kind for a in dm["actions"]] == [ActionKind.APPROVE, ActionKind.REJECT]
    w.approvals["request_credit_line"] = dec.approval_request_id


# -- step 6: Approve -> SIMULATED, in the history and the API ----------------------------------------------
def test_step6_only_the_approver_can_answer(world):
    w = world
    apr = w.approvals["request_credit_line"]
    with pytest.raises(PermissionError):
        w.channel.click(apr, True, OFFICE)
    with w.factory() as r:
        assert r.get_approval_request(apr).status == ApprovalStatus.PENDING


def test_step6_approve_executes_simulated_and_shows_in_the_api(world):
    w = world
    apr = w.approvals["request_credit_line"]
    w.channel.click(apr, True, CEO)
    (dec,) = decisions(w, "request_credit_line")
    assert dec.status == DecisionStatus.EXECUTED and dec.executed_simulated and dec.actor == CEO
    confirmation = w.channel.to(CEO)[-1]
    assert confirmation["level"] == SIMULATED and "SIMULATED" in confirmation["text"]
    (item,) = api_decisions(w, action_type="request_credit_line")
    assert item["level"] == "APPROVAL" and item["status"] == "executed" and item["executed_simulated"] is True
    assert w.api.get(f"/v1/approvals/{apr}").json()["status"] == "approved"
    sent = len(w.channel.sent)
    w.channel.click(apr, True, CEO)  # a double click does nothing
    assert len(w.channel.sent) == sent


def test_step6_reject_from_the_dashboard_goes_through_the_same_agent(world):
    w = world
    apr = w.approvals["make_investment"]
    r = w.api.post(f"/v1/approvals/{apr}:reject", json={"by": CEO})
    assert r.status_code == 200, r.text
    (dec,) = decisions(w, "make_investment")
    assert dec.status == DecisionStatus.REJECTED and not dec.executed_simulated
    assert w.api.get("/v1/kpis").json()["pending_approvals"] == 0


# -- step 7: a sentence from the CEO becomes a rule -------------------------------------------------------
def test_step7_ceo_sentence_becomes_a_rule(world):
    w = world
    sentence = "sprzęt zawsze zatwierdzam sam"
    assert "CEO" in w.channel.say(OFFICE, sentence).text
    assert w.api.get("/v1/rules").json()["items"] == []  # only the CEO changes the policy
    reply = w.channel.say(CEO, sentence)
    assert "equipment" in reply.text and "APPROVAL" in reply.text
    (rule,) = w.api.get("/v1/rules").json()["items"]
    assert rule["text"] == sentence and rule["level"] == "APPROVAL" and rule["category"] == "equipment"
    assert rule["created_by"] == CEO and rule["active"] is True
    (dec,) = decisions(w, "create_rule")
    assert dec.actor == CEO and dec.payload["rule_id"] == rule["id"]
    # the rule changes what the policy engine says about equipment
    from cfo.agent.policy import PolicyAction, classify, context_from_transactions

    with w.factory() as r:
        ctx = context_from_transactions(r.list_transactions(), r.list_rules())
    assert classify(PolicyAction(action_type="pay_invoice", amount=Decimal("5"), category="equipment"),
                    ctx).level == PolicyLevel.APPROVAL


def test_small_talk_gets_a_reply_and_changes_nothing(world):
    w = world
    n = len(decisions(w))
    reply = w.channel.say(CEO, "hello there")
    assert reply is not None and reply.text
    assert len(decisions(w)) == n and len(w.api.get("/v1/rules").json()["items"]) == 1


# -- every step showed its level --------------------------------------------------------------------------
def test_history_shows_every_level(world):
    w = world
    by_type = {d["action_type"]: d for d in api_decisions(w)}
    assert by_type["attach_invoice"]["level"] == "AUTO"
    assert by_type["request_invoice"]["level"] == "NOTIFY"
    assert by_type["alert_cost_anomaly"]["level"] == "NOTIFY"
    assert by_type["request_credit_line"]["level"] == "APPROVAL"
    assert by_type["request_credit_line"]["executed_simulated"] is True
    assert {m["level"] for m in w.channel.sent} == {"AUTO", "NOTIFY", "APPROVAL", "SIMULATED"}


# -- the entrypoint ---------------------------------------------------------------------------------------
def test_worker_loop_ticks_the_simulator_and_starts_the_channel(world):
    w = world
    stop = threading.Event()
    rounds = []

    def wait(seconds):
        rounds.append(seconds)
        w.now[0] += seconds
        if len(rounds) >= 3:
            stop.set()

    n = len(decisions(w))
    w.worker.run(stop, wait=wait)
    assert len(rounds) == 3 and w.channel.started is False  # started for the loop, stopped afterwards
    assert len(decisions(w)) == n  # replay finished, checks deduplicated: nothing new


def test_events_of_the_replay_get_the_transaction_id_the_agent_needs():
    from cfo.domain import Event
    from cfo.run import normalize_event

    ev = Event(kind=EventKind.BANK_TRANSACTION, payload={"transaction": {"id": "txn_9", "amount": "-1"}})
    out = normalize_event(ev)
    assert out.id == ev.id and out.payload["transaction_id"] == "txn_9"
    assert normalize_event(Event(kind=EventKind.METRIC, payload={"x": 1})).payload == {"x": 1}


def test_main_has_help():
    from cfo.run import main

    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0
