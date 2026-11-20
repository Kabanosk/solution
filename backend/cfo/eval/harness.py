"""Plays one scenario against the real agent in a company of its own.

`EvalWorld` owns a Postgres schema (dropped on close). For each scenario it is emptied and seeded with the same
small company plus the scenario's `seed`; the workstation folders are a temporary directory; people are a
`RecordingNotifier`. The agent is the production one (`cfo.agent.graph.build_agent`) on the in-process tools,
the LLM is whatever the caller passes: `scripted_llm()` (a `FakeLLM` that reads the documents and facts it is
given) or the configured endpoint.

`run_scenario` returns the `outputs` dict the scorers read: level, actions, decisions, messages, a snapshot of
the decision history after every step, latency.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

import psycopg

from cfo import observability as obs
from cfo.agent.graph import Agent, AgentConfig, build_agent
from cfo.agent.policy import PolicyAction, classify, context_from_transactions
from cfo.agent.ports import ActionButton
from cfo.agent.tools import InProcessToolBox
from cfo.db.migrate import SCHEMA_PATH
from cfo.domain import (
    CostMetric,
    Employee,
    Event,
    EventKind,
    PolicyLevel,
    Rule,
    Team,
    Transaction,
    Vacation,
)
from cfo.eval.scenarios import Scenario, Seed
from cfo.llm import LLM, FakeLLM, Message, ToolSpec
from cfo.mcp_servers.common import make_repo_factory
from cfo.mcp_servers.company_db import CompanyDbTools
from cfo.mcp_servers.hr import HrTools
from cfo.mcp_servers.workstations import DbAccessLog, WorkstationTools

CEO, OFFICE = "vincenzo", "grazyna"
START = datetime(2025, 12, 10, 9, 0, tzinfo=UTC)  # first step; every later step is one simulated day on
DECEMBER = Vacation(start=date(2025, 12, 1), end=date(2025, 12, 31))
LEVEL_ORDER = [lvl.value for lvl in PolicyLevel]  # AUTO < NOTIFY < APPROVAL

# Base company. Bank statement Aug-Dec 2025: 100,000 EUR of inflows a month (so 1% = 1,000 EUR and 5% = 5,000 EUR
# for D-005), one food supplier at about 600 EUR and the rent. All of it is documented: the agent has nothing to do
# until a scenario adds something.
INFLOW = Decimal("100000.00")
ASIMEX = "Asimex Wholesale Ltd"
BASE_SUPPLIER_PAYMENTS = {ASIMEX: ["580.00", "600.00", "620.00", "600.00"], "Immobilien Amalfi GmbH": ["8000.00"] * 4}


class RecordingNotifier:
    """`cfo.agent.ports.Notifier` that keeps what the agent told whom."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_dm(self, employee_id: str, text: str, level: PolicyLevel | str, actions: Sequence[ActionButton] = ()) -> str:
        self.sent.append({"to": employee_id, "level": str(getattr(level, "value", level)), "text": text,
                          "buttons": [a.action_id for a in actions]})
        return f"eval:{len(self.sent)}"


# -- the scripted model ------------------------------------------------------------------------------------
_INVOICE_NO = re.compile(r"INVOICE\s+(\S+)", re.IGNORECASE)
_TOTAL = re.compile(r"^Total \(EUR\)\s+([\d.,]+)\s*$", re.IGNORECASE | re.MULTILINE)
_LINE = re.compile(r"^(?P<d>.+?) (?P<q>\d+) (?P<u>\d+\.\d{2}) (?P<a>\d+\.\d{2})$")


def _scripted(messages: Sequence[Message], tools: Sequence[ToolSpec] | None) -> Any:
    """Plays a careful model: reads the invoice fields that are in the document, words the facts it was given."""
    names = {t.name for t in tools or []}
    user = messages[-1].content or ""
    if "submit_invoice" in names:
        number, total = _INVOICE_NO.search(user), _TOTAL.search(user)
        if not (number and total):
            return "I cannot read an invoice in this document."
        lines = [{"description": m["d"], "quantity": m["q"], "unit_price": m["u"], "amount": m["a"]}
                 for m in map(_LINE.match, user.splitlines()) if m]
        supplier = next((ln.strip() for ln in user.splitlines() if re.search(r"(Ltd|GmbH|SRL)$", ln.strip())), None)
        return FakeLLM.tool_call("submit_invoice", number=number.group(1), supplier=supplier, total=total.group(1),
                                 lines=lines)
    if "search_queries" in names:
        counterparty = re.search(r"Counterparty: (\S+)", user)
        return FakeLLM.tool_call("search_queries", queries=[counterparty.group(1) if counterparty else "invoice"])
    facts = {}
    if "Facts: " in user:
        try:
            facts = json.loads(user.split("Facts: ", 1)[1])
        except ValueError:
            facts = {}
    flat = []
    for key, value in facts.items():
        if isinstance(value, dict):
            flat += [f"{key} {k} {v}" for k, v in value.items()]
        elif value is not None:
            flat.append(f"{key.replace('_', ' ')} {value}")
    return ("In short: " + "; ".join(flat) + ".")[:590] if flat else "This is a plain explanation of the facts."


def scripted_llm() -> FakeLLM:
    """The `--llm fake` model. Deterministic, so a failing scenario points at the code, not at the model."""
    return FakeLLM(handler=_scripted)


# -- the world ---------------------------------------------------------------------------------------------
class EvalWorld:
    def __init__(self, database_url: str, llm: LLM, schema: str | None = None):
        self.url = database_url
        self.llm = llm
        self.schema = schema or f"cfo_eval_{os.getpid()}"
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", self.schema):
            raise ValueError(f"bad schema name {self.schema!r}")
        self.factory = make_repo_factory(database_url, schema=self.schema)
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.notifier = RecordingNotifier()
        self.agent: Agent | None = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def reset(self, seed: Seed) -> None:
        """Empty company -> base company + `seed`, a fresh agent and notifier."""
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
            conn.execute(f"CREATE SCHEMA {self.schema}")
        with self.factory() as repo:
            repo.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            repo.conn.commit()
            self._seed(repo, seed)
        if self._tmp is not None:
            self._tmp.cleanup()
        self._tmp = tempfile.TemporaryDirectory(prefix="cfo-eval-ws-")
        root = Path(self._tmp.name)
        (root / OFFICE / "Downloads").mkdir(parents=True)
        (root / OFFICE / "Mail").mkdir()
        for rel, text in seed.files.items():
            target = root / OFFICE / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        tools = InProcessToolBox(CompanyDbTools(self.factory), WorkstationTools(root, DbAccessLog(self.factory)),
                                 HrTools(self.factory))
        self.notifier = RecordingNotifier()
        self.agent = build_agent(tools, self.factory, self.llm, self.notifier, AgentConfig(reminder_after_hours=24))

    @staticmethod
    def _seed(repo: Any, seed: Seed) -> None:
        repo.add_team(Team(id="kitchen", name="Kitchen", owner_id="rosa"))
        repo.add_team(Team(id="it", name="IT", owner_id=CEO))
        managers = {"rosa": CEO, OFFICE: "rosa", **seed.managers}
        people = [("vincenzo", "Vincenzo", "CEO", None), ("rosa", "Rosa", "head chef", "kitchen"),
                  (OFFICE, "Grazyna", "office manager", None)]
        for emp_id, name, role, team in people:
            repo.add_employee(Employee(id=emp_id, name=name, role=role, team_id=team, manager_id=managers.get(emp_id),
                                       vacations=[DECEMBER] if emp_id in seed.away else []))
        n = 0
        for month in (8, 9, 10, 11, 12):
            n += 1
            repo.add_transaction(Transaction(id=f"base_{n}", booking_date=date(2025, month, 1), type="CARD SETTLEMENT",
                                             reference="Card settlements", counterparty="Card acquirer", amount=INFLOW,
                                             category="sales", documented=True))
        for supplier, amounts in BASE_SUPPLIER_PAYMENTS.items():
            for month, amount in zip((8, 9, 10, 11), amounts, strict=True):
                n += 1
                repo.add_transaction(Transaction(id=f"base_{n}", booking_date=date(2025, month, 5), type="GIRO",
                                                 reference=f"{supplier} {month:02d}/2025", counterparty=supplier,
                                                 amount=-Decimal(amount), documented=True))
        for t in seed.transactions:
            row = {"type": "GIRO", "category": "food", "team_id": "kitchen", **t}
            row["booking_date"] = row.pop("date")
            row.setdefault("reference", f"Invoice {row.get('invoice_number')}")
            repo.add_transaction(Transaction(**row))
        for r in seed.rules:
            repo.add_rule(Rule(**r))
        for m in seed.cost_metrics:
            repo.add_cost_metric(CostMetric(**m))

    # -- reading the result ------------------------------------------------------------------------------
    def decisions(self) -> list[dict[str, Any]]:
        """Oldest first, each with the status of its approval request (None when it has none)."""
        with self.factory() as repo:
            rows = repo._all(
                "SELECT d.*, a.status AS approval_status FROM decisions d "
                "LEFT JOIN approval_requests a ON a.id = d.approval_request_id ORDER BY d.ts, d.id")
        return [{"id": r["id"], "action_type": r["action_type"], "level": str(r["level"]), "status": str(r["status"]),
                 "executed_simulated": bool(r["executed_simulated"]), "approval_status": r["approval_status"],
                 "recipient": (r["payload"] or {}).get("recipient"), "summary": r["summary"], "reason": r["reason"]}
                for r in rows]

    def documented(self) -> dict[str, bool]:
        with self.factory() as repo:
            return {t.id: t.documented for t in repo.list_transactions() if not t.id.startswith("base_")}


def _event(step: dict[str, Any], ts: datetime) -> Event:
    payload = dict(step.get("payload") or {})
    if step.get("transaction_id"):
        payload["transaction_id"] = step["transaction_id"]
    return Event(ts=ts, kind=EventKind(step["kind"]), payload=payload)


def run_scenario(world: EvalWorld, scenario: Scenario) -> dict[str, Any]:
    """Seed, play the steps, return the outputs. An exception in a step ends the scenario and is reported in
    `error` (every scorer then fails it); it is not raised, so one broken scenario does not stop the run."""
    world.reset(scenario.seed)
    assert world.agent is not None
    calls_before = len(getattr(world.llm, "calls", []) or [])
    snapshots: list[list[dict[str, Any]]] = []
    policy: list[dict[str, Any]] = []
    outcome, approval_id, error = None, None, None
    ts = START
    started = time.perf_counter()
    try:
        for step in scenario.steps:
            (kind, spec), = step.items()
            for _ in range(int(spec.get("repeat", 1)) if kind == "event" else 1):
                if kind == "event":
                    res = world.agent.handle(_event(spec, ts))
                    outcome, approval_id = res.status, res.approval_id or approval_id
                elif kind == "answer":
                    if approval_id is None:
                        raise RuntimeError("answer step, but no approval is pending")
                    outcome = world.agent.resume_approval(approval_id, bool(spec["approved"]), spec.get("by", CEO)).status
                else:
                    with world.factory() as repo:
                        context = context_from_transactions(repo.list_transactions(), repo.list_rules())
                    action = PolicyAction(**spec)
                    with obs.span("policy.classify", inputs=action) as sp:
                        verdict = classify(action, context)
                        sp.set_outputs(verdict)
                    policy.append({"action_type": action.action_type, "level": verdict.level.value, "reason": verdict.reason})
                    outcome = "classified"
                snapshots.append(world.decisions())
                ts += timedelta(days=1)
    except Exception as exc:  # noqa: BLE001 - reported per scenario
        error = f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - started

    decisions = world.decisions()
    levels = [d["level"] for d in decisions] + [p["level"] for p in policy]
    messages = list(world.notifier.sent)
    return {
        "scenario": scenario.id,
        "target": scenario.target,
        "level": max(levels, key=LEVEL_ORDER.index) if levels else "NONE",
        "actions": [d["action_type"] for d in decisions],
        "outcome": outcome,
        "decisions": decisions,
        "policy": policy,
        "messages": messages,
        "texts": [m["text"] for m in messages] + [p["reason"] for p in policy],
        "documented": world.documented(),
        "snapshots": snapshots,
        "latency_s": round(latency, 4),
        "llm_calls": len(getattr(world.llm, "calls", []) or []) - calls_before,
        "error": error,
    }
