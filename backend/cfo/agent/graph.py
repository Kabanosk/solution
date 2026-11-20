"""The CFO agent: a LangGraph that handles one `Event` at a time (demo flows 2-3 and 5-6).

    BANK_TRANSACTION / INVOICE_DOCUMENT / REMINDER  -> invoice reconciliation (D-003)
        load_txn -> find_docs -> match -> attach | ask      (ask = request / remind / escalate along the HR chain)
    METRIC {signal: "liquidity_forecast", ...}      -> credit line proposal -> approval (interrupt) (D-005)
    METRIC (anything else)                          -> investigation sub-agent -> cost anomaly alerts (D-006)

Deterministic in code: which payment/document matches, amount comparison, who is asked next, the policy level
(`cfo.agent.policy.classify`), the approval interrupt and resume. The LLM (via `cfo.llm` only) extracts invoice
lines from document text, words extra search queries and explains facts in plain language.

Approvals: `request_approval` writes the pending Decision + ApprovalRequest and DMs the approver (side effects
happen in a node of their own, because LangGraph re-runs a node from its start on resume); `await_approval` only
calls `interrupt()`. `Agent.resume_approval(approval_id, approved, by)` resumes the saved thread. Execution after
approval is only recorded as SIMULATED (`Decision.executed_simulated`).

Threading: `handle` and `resume_approval` take one lock, so Slack handlers may call from worker threads.
"""

from __future__ import annotations

import logging
import operator
import re
import threading
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, TypedDict

import psycopg
from cfo import observability as obs
from cfo.agent.documents import collect_text
from cfo.agent.investigate import build_investigation_graph
from cfo.agent.llm_tasks import (alternative_queries, explain, extract_invoice,
                                 parse_amount)
from cfo.agent.policy import (PolicyAction, PolicyContext, classify,
                              context_from_transactions)
from cfo.agent.ports import ActionButton, Notifier
from cfo.agent.tools import Gateway, ToolBox
from cfo.db.repo import Repository
from cfo.domain import (ApprovalRequest, ApprovalStatus, Decision,
                        DecisionStatus, Event, EventKind, PolicyLevel,
                        Transaction, new_id)
from cfo.llm import LLM
from cfo.usage import recorder as usage_recorder
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

LIQUIDITY_ACTIONS = {
    "request_credit_line": {
        "key": "credit_line",
        "label": "credit line",
        "summary": "Request a credit line of {amount} before {month}",
        "instruction": "Explain why the company should ask the bank for a credit line now.",
        "fallback": "The forecast shows the balance dropping to {low} EUR around {month}; a credit line of "
        "{amount} would cover the gap.",
    },
    "make_investment": {
        "key": "investment",
        "label": "investment",
        "summary": "Invest {amount} in a money market fund ahead of {month}",
        "instruction": "Explain why the company can invest this sum now.",
        "fallback": "The forecast stays above the safety buffer, so {amount} can be invested ahead of {month}.",
    },
}

ASK_TYPES = frozenset(
    {"request_invoice", "request_clarification", "remind_invoice", "escalate_invoice"}
)
EXHAUSTED = "escalation_exhausted"


class AgentConfig(BaseModel):
    ceo_id: str = "vincenzo"
    document_owner_id: str = (
        "grazyna"  # whose company folders hold supplier invoices (D-003, D-004)
    )
    reminder_after_hours: float = (
        24.0  # the caller schedules a REMINDER event this long after a request
    )
    max_candidates: int = 5
    anomaly_threshold_pct: float = 20.0


class HandleResult(BaseModel):
    status: Literal["done", "awaiting_approval", "ignored", "already_resolved"] = "done"
    decision_ids: list[str] = Field(default_factory=list)
    approval_id: str | None = None
    next_reminder_hours: float | None = (
        None  # schedule a REMINDER event for the same transaction
    )
    steps: list[str] = Field(default_factory=list)


class AgentState(TypedDict, total=False):
    event: dict[str, Any]
    txn: dict[str, Any]
    candidates: list[dict[str, Any]]
    outcome: str  # matched | mismatch | not_found
    found: dict[str, Any]
    pending: dict[str, Any]
    approval: dict[str, Any]
    findings: list[dict[str, Any]]
    next_reminder_hours: float | None
    decision_ids: Annotated[list[str], operator.add]
    steps: Annotated[list[str], operator.add]


class Deps:
    def __init__(
        self,
        tools: ToolBox,
        repo_factory: Callable[[], Repository],
        llm: LLM,
        notifier: Notifier,
        config: AgentConfig,
    ):
        self.gw = Gateway(tools)
        self.repo_factory = repo_factory
        self.llm = obs.traced_llm(
            llm
        )  # every chat call is a span under the node that made it (T027)
        self.notifier = notifier
        self.cfg = config

    def dm(
        self,
        employee_id: str,
        text: str,
        level: PolicyLevel | str,
        actions: Sequence[ActionButton] = (),
    ) -> str | None:
        try:
            return self.notifier.send_dm(employee_id, text, level, actions)
        except (
            Exception
        ):  # noqa: BLE001 - a Slack hiccup must not lose the decision that was already recorded
            log.exception("DM to %s failed", employee_id)
            return None

    def save_decision(self, dec: Decision) -> str:
        with self.repo_factory() as repo:
            repo.add_decision(dec)
        return dec.id

    def name(self, employee_id: str, day: date) -> str:
        try:
            emp = self.gw.employee(employee_id, day)
        except Exception:  # noqa: BLE001
            emp = None
        return (emp or {}).get("name") or employee_id

    def policy_context(self) -> PolicyContext:
        # the full statement, not the tool's 500-row page: thresholds and typical amounts need all of it
        with self.repo_factory() as repo:
            return context_from_transactions(
                repo.list_transactions(), repo.list_rules()
            )


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.casefold())


def _eur(v: Decimal | str) -> str:
    return f"{Decimal(v):,.2f} EUR"


def _event(state: AgentState) -> Event:
    return Event(**state["event"])


def _txn(state: AgentState) -> Transaction:
    return Transaction(**state["txn"])


def _get_decision(repo: Repository, decision_id: str) -> Decision | None:
    rows = repo._all("SELECT * FROM decisions WHERE id = %s", (decision_id,))
    return Decision(**rows[0]) if rows else None


# ---------------------------------------------------------------------------------------------------
def build_graph(d: Deps, checkpointer: BaseCheckpointSaver):
    cfg, gw = d.cfg, d.gw
    investigation = build_investigation_graph(gw, d.llm, cfg.anomaly_threshold_pct)

    def route(state: AgentState) -> str:
        ev = _event(state)
        if ev.kind in (
            EventKind.BANK_TRANSACTION,
            EventKind.INVOICE_DOCUMENT,
            EventKind.REMINDER,
        ):
            return "load_txn"
        if ev.kind == EventKind.METRIC:
            return (
                "propose_credit_line"
                if ev.payload.get("signal") == "liquidity_forecast"
                else "investigate"
            )
        return "ignore"

    def ignore(state: AgentState) -> AgentState:
        return {"steps": [f"ignored {state['event'].get('kind')}"]}

    # -- reconciliation ------------------------------------------------------------------------
    def load_txn(state: AgentState) -> AgentState:
        txn_id = _event(state).payload.get("transaction_id")
        txn = gw.transaction(txn_id) if txn_id else None
        if txn is None:
            return {"steps": ["transaction unknown"]}
        if txn.documented or not txn.invoice_number or txn.amount >= 0:
            return {"steps": [f"{txn.id}: nothing to reconcile"]}
        return {
            "txn": txn.model_dump(mode="json"),
            "steps": [f"{txn.id}: reconcile {txn.invoice_number}"],
        }

    def find_docs(state: AgentState) -> AgentState:
        txn, owner = _txn(state), cfg.document_owner_id
        reason = f"find invoice {txn.invoice_number} for payment {txn.id}"
        files = gw.search_files(owner, txn.invoice_number, reason)
        if not files:
            for q in alternative_queries(
                d.llm, txn.counterparty, txn.reference, str(abs(txn.amount))
            ):
                files += gw.search_files(owner, q, reason)
        seen: set[str] = set()
        candidates = []
        for f in files:
            if f["path"] in seen or len(seen) >= cfg.max_candidates:
                continue
            seen.add(f["path"])
            text = collect_text(gw, owner, f["path"], reason)
            if _norm(txn.invoice_number) in _norm(
                text
            ):  # the document must name this invoice
                candidates.append({"path": f["path"], "text": text})
        return {
            "candidates": candidates,
            "steps": [f"searched {owner}'s folders: {len(candidates)} candidate(s)"],
        }

    def match(state: AgentState) -> AgentState:
        txn = _txn(state)
        expected = abs(txn.amount)
        first_mismatch: dict[str, Any] | None = None
        for c in state["candidates"]:
            ex = extract_invoice(d.llm, c["text"])
            if ex is None:
                continue
            note = None
            lines = ex.lines
            if lines and sum((ln.amount for ln in lines), Decimal(0)) != ex.total:
                lines, note = (
                    [],
                    "line amounts do not add up to the total, lines not stored",
                )
            found = {
                "path": c["path"],
                "number": txn.invoice_number,
                "supplier": ex.supplier or txn.counterparty,
                "issue_date": (ex.issue_date or txn.booking_date).isoformat(),
                "total": str(ex.total),
                "lines": [ln.model_dump(mode="json") for ln in lines],
                "note": note,
                "difference": str(ex.total - expected),
            }
            if ex.total == expected:
                return {
                    "outcome": "matched",
                    "found": found,
                    "steps": [f"{c['path']}: amount matches"],
                }
            first_mismatch = first_mismatch or found
        if first_mismatch:
            return {
                "outcome": "mismatch",
                "found": first_mismatch,
                "steps": ["document amount differs from payment"],
            }
        return {"outcome": "not_found", "steps": ["no usable document"]}

    def after_match(state: AgentState) -> str:
        return "attach" if state["outcome"] == "matched" else "ask"

    def attach(state: AgentState) -> AgentState:
        txn, f, ev = _txn(state), state["found"], _event(state)
        pol = classify(PolicyAction(action_type="attach_invoice"), d.policy_context())
        args = {
            "transaction_id": txn.id,
            "number": f["number"],
            "supplier": f["supplier"],
            "issue_date": f["issue_date"],
            "total": f["total"],
            "lines": f["lines"],
            "source": f["path"],
        }
        if (
            pol.level == PolicyLevel.APPROVAL
        ):  # only possible through a CEO rule on attach_invoice
            return {
                "pending": {
                    "action_type": "attach_invoice",
                    "kind": "attach_invoice",
                    "args": args,
                    "reason": pol.reason,
                    "summary": f"Attach invoice {f['number']} from {f['path']} to payment {txn.id}",
                    "question": f"May I attach invoice {f['number']} ({_eur(f['total'])}) found at {f['path']}?",
                    "facts": {
                        "invoice": f["number"],
                        "total": f["total"],
                        "path": f["path"],
                    },
                }
            }
        res = gw.attach_invoice(**args)
        if not res.get("ok"):
            return {
                "outcome": "mismatch",
                "found": {**f, "error": res.get("error")},
                "steps": [f"attach refused: {res.get('error')}"],
            }
        owner = cfg.document_owner_id
        text = (
            f"I took invoice {f['number']} ({_eur(f['total'])}, {f['supplier']}) from your company folder "
            f"({f['path']}) and added it to the database for the payment of {txn.booking_date.isoformat()}. "
            "I only look at company folders and every access is logged."
        )
        dec = Decision(
            action_type="attach_invoice",
            level=pol.level,
            reason=pol.reason,
            actor="agent",
            summary=f"Found invoice {f['number']} in {owner}'s folder {f['path']}, amounts match, payment documented.",
            payload={
                "transaction_id": txn.id,
                "invoice_number": f["number"],
                "path": f["path"],
                "invoice_id": res.get("invoice_id"),
                "lines": len(f["lines"]),
                "note": f.get("note"),
            },
        )
        d.save_decision(dec)
        d.dm(owner, text, pol.level)
        return {
            "decision_ids": [dec.id],
            "steps": [f"attached {f['number']} ({pol.level})"],
        }

    def after_attach(state: AgentState) -> str:
        if state.get("pending"):
            return "request_approval"
        return "ask" if state.get("outcome") == "mismatch" else END

    def ask(state: AgentState) -> AgentState:
        txn, ev, owner = _txn(state), _event(state), cfg.document_owner_id
        day = ev.ts.date()
        reason = state["outcome"]  # not_found | mismatch
        res = gw.escalation_chain(owner, day)
        chain = (
            res["chain"]
            if res.get("ok")
            else [
                {"id": owner, "name": owner, "on_vacation": False},
                {"id": cfg.ceo_id, "name": cfg.ceo_id, "on_vacation": False},
            ]
        )
        asks = [
            x
            for x in reversed(gw.decisions())
            if x.payload.get("transaction_id") == txn.id
        ]
        history = [x for x in asks if x.action_type in ASK_TYPES]
        if any(x.action_type == EXHAUSTED for x in asks):
            return {"steps": ["escalation already exhausted"]}
        if history and ev.kind != EventKind.REMINDER:
            return {"steps": ["already requested, waiting for the reminder timer"]}

        f = state.get("found") or {}
        if reason == "mismatch":
            what = (
                (
                    f"The document {f.get('path')} for invoice {txn.invoice_number} shows {_eur(f['total'])}, but the "
                    f"payment was {_eur(abs(txn.amount))} (difference {_eur(f['difference'])})."
                )
                if "total" in f
                else (
                    f"The document for invoice {txn.invoice_number} could not be attached ({f.get('error')})."
                )
            )
            request = "Please explain the difference or send the correct invoice."
            first_action = "request_clarification"
        else:
            what = (
                f"Payment of {_eur(abs(txn.amount))} to {txn.counterparty} on {txn.booking_date.isoformat()} "
                f"(invoice {txn.invoice_number}) has no invoice on file, and I found none in "
                f"{d.name(owner, day)}'s company folders. Without it the expense is a tax risk (Belegpflicht)."
            )
            request = "Please send me the invoice."
            first_action = "request_invoice"
        why = explain(
            d.llm,
            "Say why a paid expense without an invoice matters.",
            {
                "invoice": txn.invoice_number,
                "rule": "Belegpflicht (German receipt obligation), tax risk",
            },
            "A paid expense without a receipt is a tax risk (Belegpflicht).",
        )

        skipped: list[str] = []
        prev_name = None
        if not history:
            target = next((c for c in chain if not c["on_vacation"]), chain[-1])
            skipped = [c["id"] for c in chain[: chain.index(target)]]
            action = first_action
            if skipped:
                prev_name = d.name(owner, day)
                lead = (
                    f"{prev_name} is on vacation, so I am asking you. "
                    if target["id"] != owner
                    else ""
                )
                text = f"{lead}{what} {request} {why}"
            else:
                text = f"{what} {request} {why}"
        else:
            last = history[-1]
            last_to = last.payload.get("recipient")
            if last.action_type in ("remind_invoice",):
                ids = [c["id"] for c in chain]
                i = ids.index(last_to) if last_to in ids else len(ids) - 1
                nxt = [c for c in chain[i + 1 :] if not c["on_vacation"]]
                if not nxt:
                    return _exhausted(txn, last_to, chain, day)
                target, action = nxt[0], "escalate_invoice"
                prev_name = d.name(last_to, day)
                text = (
                    f"{prev_name} has not resolved this after {cfg.reminder_after_hours:g} h, so I am escalating to "
                    f"you. {what} {request} {why}"
                )
            else:
                target = next((c for c in chain if c["id"] == last_to), chain[-1])
                action = "remind_invoice"
                text = f"Reminder: {what} {request}"

        d.dm(target["id"], text, PolicyLevel.NOTIFY)
        ids_out: list[str] = []
        dec = Decision(
            action_type=action,
            level=PolicyLevel.NOTIFY,
            actor="agent",
            status=(
                DecisionStatus.ESCALATED
                if action == "escalate_invoice"
                else DecisionStatus.EXECUTED
            ),
            reason=(
                "Asking people for a missing document moves no money; it is routine follow-up (D-003)."
                if not skipped
                else f"{', '.join(skipped)} on vacation, so I went straight to the next person (D-003)."
            ),
            summary=f"{action.replace('_', ' ').capitalize()} for {txn.invoice_number}: asked {target['name']}.",
            payload={
                "transaction_id": txn.id,
                "recipient": target["id"],
                "reason": reason,
                "skipped_on_vacation": skipped,
                "remind_after_hours": cfg.reminder_after_hours,
            },
        )
        ids_out.append(d.save_decision(dec))
        if ev.kind != EventKind.REMINDER and target["id"] != owner:
            d.dm(
                owner,
                "I searched your company folders (Downloads, Mail) for invoice "
                f"{txn.invoice_number} and found {'no usable document' if reason == 'not_found' else 'a document with a different amount'}. "
                "Access is logged; I only read company folders.",
                PolicyLevel.NOTIFY,
            )
        return {
            "decision_ids": ids_out,
            "next_reminder_hours": cfg.reminder_after_hours,
            "steps": [f"{action} -> {target['id']}"],
        }

    def _exhausted(
        txn: Transaction, last_to: str | None, chain: list[dict[str, Any]], day: date
    ) -> AgentState:
        ceo = chain[-1]["id"]
        dec = Decision(
            action_type=EXHAUSTED,
            level=PolicyLevel.NOTIFY,
            status=DecisionStatus.ESCALATED,
            actor="agent",
            reason="Everybody in the escalation chain was asked and the invoice is still missing (D-003).",
            summary=f"Nobody provided invoice {txn.invoice_number}; the whole chain was asked.",
            payload={"transaction_id": txn.id, "recipient": ceo},
        )
        d.save_decision(dec)
        d.dm(
            ceo,
            f"Nobody could provide invoice {txn.invoice_number} ({_eur(abs(txn.amount))} to {txn.counterparty}). "
            "Without it the payment is a tax risk (Belegpflicht). Please decide how to handle it.",
            PolicyLevel.NOTIFY,
        )
        return {"decision_ids": [dec.id], "steps": ["escalation exhausted"]}

    # -- credit line proposal ------------------------------------------------------------------
    def propose_credit_line(state: AgentState) -> AgentState:
        # Also handles the investment proposal of the same signal (T016): `action_type` in the payload picks it.
        p = _event(state).payload
        amount = parse_amount(p.get("requested_amount"))
        if amount is None:
            return {"steps": ["liquidity signal without requested_amount"]}
        action = (
            p.get("action_type")
            if p.get("action_type") in LIQUIDITY_ACTIONS
            else "request_credit_line"
        )
        what = LIQUIDITY_ACTIONS[action]
        month, low = str(p.get("month", "")), p.get("min_balance")
        pol = classify(
            PolicyAction(action_type=action, amount=amount), d.policy_context()
        )
        facts = {
            "forecast_month": month,
            "lowest_forecast_balance_eur": low,
            f"{what['key']}_eur": str(amount),
            "why": p.get("why"),
        }
        if p.get(
            "why"
        ):  # T016 wrote a plain-language reason from code-computed numbers; keep it verbatim
            explanation = str(p["why"])
        else:
            explanation = explain(
                d.llm,
                what["instruction"],
                facts,
                what["fallback"].format(amount=_eur(amount), month=month, low=low),
            )
        summary = what["summary"].format(amount=_eur(amount), month=month)
        pending = {
            "action_type": action,
            "kind": "simulate",
            "args": {"amount": str(amount)},
            "reason": pol.reason,
            "summary": summary,
            "facts": facts,
            "question": f"{summary}. {explanation}",
        }
        if pol.level == PolicyLevel.APPROVAL:
            return {"pending": pending, "steps": [f"{what['label']} needs approval"]}
        dec = Decision(
            action_type=action,
            level=pol.level,
            reason=pol.reason,
            summary=summary + " (SIMULATED)",
            payload={"facts": facts},
            executed_simulated=True,
        )
        d.save_decision(dec)
        if pol.level == PolicyLevel.NOTIFY:
            d.dm(
                cfg.ceo_id,
                f"{summary}. {explanation} Execution is SIMULATED.",
                pol.level,
            )
        return {
            "decision_ids": [dec.id],
            "steps": [f"{what['label']} {pol.level}, simulated"],
        }

    # -- approvals -----------------------------------------------------------------------------
    def request_approval(state: AgentState) -> AgentState:
        pending, ev = state["pending"], _event(state)
        apr_id, dec_id = new_id("apr"), new_id("dec")
        dec = Decision(
            id=dec_id,
            action_type=pending["action_type"],
            summary=pending["summary"],
            level=PolicyLevel.APPROVAL,
            reason=pending["reason"],
            status=DecisionStatus.PENDING,
            approval_request_id=apr_id,
            payload={"thread_id": f"evt:{ev.id}", "pending": pending},
        )
        req = ApprovalRequest(
            id=apr_id,
            decision_id=dec_id,
            approver_id=cfg.ceo_id,
            question=pending["question"],
        )
        d.save_decision(dec)
        with d.repo_factory() as repo:
            repo.add_approval_request(req)
        ref = d.dm(
            cfg.ceo_id,
            pending["question"],
            PolicyLevel.APPROVAL,
            [
                ActionButton(action_id="approve", label="Approve", value=apr_id),
                ActionButton(action_id="reject", label="Reject", value=apr_id),
            ],
        )
        if ref:
            req.channel_ref = ref
            with d.repo_factory() as repo:
                repo.add_approval_request(req)
        return {
            "approval": {"approval_id": apr_id, "decision_id": dec_id},
            "decision_ids": [dec_id],
            "steps": ["waiting for approval"],
        }

    def await_approval(state: AgentState) -> AgentState:
        ids, pending = state["approval"], state["pending"]
        answer = interrupt(
            {"approval_id": ids["approval_id"], "question": pending["question"]}
        )
        approved, by = bool(answer["approved"]), str(answer["by"])
        with d.repo_factory() as repo:
            dec = _get_decision(repo, ids["decision_id"])
            assert dec is not None
            dec.actor = by
            if approved:
                simulated = pending["kind"] == "simulate"
                if pending["kind"] == "attach_invoice":
                    res = gw.attach_invoice(**pending["args"])
                    if not res.get("ok"):
                        dec.summary += f" Approved by {by}, but attaching failed: {res.get('error')}."
                dec.status, dec.executed_simulated = DecisionStatus.EXECUTED, simulated
                dec.summary += f" Approved by {by}." + (
                    " Execution is SIMULATED." if simulated else ""
                )
                msg = f"Approved. {pending['summary']}." + (
                    " Execution is SIMULATED (nothing was sent to a bank)."
                    if simulated
                    else ""
                )
            else:
                dec.status = DecisionStatus.REJECTED
                dec.summary += f" Rejected by {by}."
                msg = f"Rejected. Nothing was done: {pending['summary']}."
            repo.add_decision(dec)
            repo.resolve_approval(
                ids["approval_id"],
                ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
            )
        # the confirmation of a simulated execution carries the SIMULATED label (demo-flow step 6)
        d.dm(
            by,
            msg,
            (
                "SIMULATED"
                if approved and pending["kind"] == "simulate"
                else PolicyLevel.APPROVAL
            ),
        )
        return {"steps": [f"approval {'approved' if approved else 'rejected'} by {by}"]}

    # -- cost anomalies (sub-agent) ------------------------------------------------------------
    def investigate(state: AgentState) -> AgentState:
        out = investigation.invoke({})
        return {
            "findings": out.get("findings", []),
            "steps": [f"sub-agent found {len(out.get('findings', []))} anomaly(ies)"],
        }

    def alert(state: AgentState) -> AgentState:
        done = {
            (x.payload.get("kind"), x.payload.get("month"))
            for x in gw.decisions()
            if x.action_type == "alert_cost_anomaly"
        }
        owners = {t["id"]: t.get("owner_id") for t in gw.teams()}
        ids_out: list[str] = []
        for f in state.get("findings", []):
            if (f["kind"], f["month"]) in done:
                continue
            pol = classify(
                PolicyAction(action_type="alert_cost_anomaly", category=f["kind"]),
                d.policy_context(),
            )
            level = (
                PolicyLevel.NOTIFY if pol.level == PolicyLevel.AUTO else pol.level
            )  # an alert always informs
            dec = Decision(
                action_type="alert_cost_anomaly",
                level=level,
                reason=(
                    "A cost rose without its metric; informing is the agent's job, no money moves (D-006)."
                ),
                summary=f["explanation"],
                payload={"kind": f["kind"], "month": f["month"], "facts": f["facts"]},
            )
            ids_out.append(d.save_decision(dec))
            for who in dict.fromkeys([cfg.ceo_id, owners.get(f.get("team_id"))]):
                if who:
                    d.dm(who, f["explanation"], level)
        return {"decision_ids": ids_out, "steps": [f"{len(ids_out)} alert(s)"]}

    g = StateGraph(AgentState)
    for name, fn in [
        ("ignore", ignore),
        ("load_txn", load_txn),
        ("find_docs", find_docs),
        ("match", match),
        ("attach", attach),
        ("ask", ask),
        ("propose_credit_line", propose_credit_line),
        ("request_approval", request_approval),
        ("await_approval", await_approval),
        ("investigate", investigate),
        ("alert", alert),
    ]:
        g.add_node(
            name, obs.traced_node(name, fn)
        )  # one MLflow span per visited node (no-op when off)
    g.add_conditional_edges(
        START, route, ["load_txn", "propose_credit_line", "investigate", "ignore"]
    )
    g.add_edge("ignore", END)
    g.add_conditional_edges(
        "load_txn", lambda s: "find_docs" if s.get("txn") else END, ["find_docs", END]
    )
    g.add_edge("find_docs", "match")
    g.add_conditional_edges("match", after_match, ["attach", "ask"])
    g.add_conditional_edges("attach", after_attach, ["request_approval", "ask", END])
    g.add_edge("ask", END)
    g.add_conditional_edges(
        "propose_credit_line",
        lambda s: "request_approval" if s.get("pending") else END,
        ["request_approval", END],
    )
    g.add_edge("request_approval", "await_approval")
    g.add_edge("await_approval", END)
    g.add_edge("investigate", "alert")
    g.add_edge("alert", END)
    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------------------------------
class Agent:
    """Facade: `handle(event)` and `resume_approval(approval_id, approved, by)`."""

    def __init__(self, deps: Deps, checkpointer: BaseCheckpointSaver | None = None):
        self._d = deps
        self._graph = build_graph(deps, checkpointer or InMemorySaver())
        self._lock = threading.RLock()

    def handle(self, event: Event) -> HandleResult:
        """Process one event. Returns `awaiting_approval` (with the approval id) when it stopped at an interrupt."""
        with obs.span(
            "agent.handle", inputs=event
        ) as sp:  # one MLflow trace per event (no-op when tracing is off)
            return self._traced(
                sp,
                self._handle(event),
                {
                    "cfo.event_kind": event.kind.value,
                    "cfo.event_id": event.id,
                    "cfo.transaction_id": event.payload.get("transaction_id"),
                },
            )

    def _handle(self, event: Event) -> HandleResult:
        if event.kind == EventKind.INVOICE_DOCUMENT and not event.payload.get(
            "transaction_id"
        ):
            # a document appeared somewhere: re-check every payment still missing its invoice
            merged = HandleResult()
            for t in self._d.gw.transactions(undocumented_only=True):
                r = self.handle(
                    Event(
                        ts=event.ts,
                        kind=event.kind,
                        payload={**event.payload, "transaction_id": t.id},
                    )
                )
                merged.decision_ids += r.decision_ids
                merged.steps += r.steps
                merged.next_reminder_hours = (
                    merged.next_reminder_hours or r.next_reminder_hours
                )
            return merged
        with self._lock:
            config = {"configurable": {"thread_id": f"evt:{event.id}"}}
            with usage_recorder.usage_context(
                event_id=event.id, event_type=event.kind.value
            ):  # T032: cost attribution
                self._graph.invoke({"event": event.model_dump(mode="json")}, config)
            result = self._result(config)
            usage_recorder.link_decision(event.id, result.decision_ids)
            return result

    def resume_approval(
        self, approval_id: str, approved: bool, by: str
    ) -> HandleResult:
        """Answer a pending approval. `by` must be the approver (PermissionError otherwise). Idempotent: a repeated
        call (double click, Slack retry) returns `already_resolved` without doing anything twice.
        """
        inputs = {"approval_id": approval_id, "approved": approved, "by": by}
        with obs.span("agent.resume_approval", inputs=inputs) as sp:
            return self._traced(
                sp,
                self._resume_approval(approval_id, approved, by),
                {
                    "cfo.event_kind": "approval_answer",
                    "cfo.approval_id": approval_id,
                    "cfo.approved": approved,
                },
            )

    def _resume_approval(
        self, approval_id: str, approved: bool, by: str
    ) -> HandleResult:
        with self._lock:
            with self._d.repo_factory() as repo:
                req = repo.get_approval_request(approval_id)
                dec = _get_decision(repo, req.decision_id) if req else None
            if req is None or dec is None:
                raise KeyError(f"unknown approval {approval_id!r}")
            if by != req.approver_id:
                raise PermissionError(
                    f"{by!r} may not answer approval {approval_id!r} (approver is {req.approver_id!r})"
                )
            if req.status != ApprovalStatus.PENDING:
                return HandleResult(
                    status="already_resolved",
                    approval_id=approval_id,
                    decision_ids=[dec.id],
                )
            config = {"configurable": {"thread_id": dec.payload["thread_id"]}}
            self._graph.invoke(Command(resume={"approved": approved, "by": by}), config)
            return self._result(config)

    def _traced(
        self, sp: obs.Span, res: HandleResult, tags: dict[str, Any]
    ) -> HandleResult:
        """Tag the current trace with what happened: outcome, final policy level, simulated execution."""
        if not obs.enabled():
            return res
        try:
            with self._d.repo_factory() as repo:
                decs = [
                    x
                    for x in (_get_decision(repo, i) for i in res.decision_ids)
                    if x is not None
                ]
            levels = [x.level for x in decs]
            final = (
                max(levels, key=list(PolicyLevel).index).value if levels else "NONE"
            )  # APPROVAL > NOTIFY > AUTO
            obs.set_trace_tags(
                {
                    **tags,
                    "cfo.outcome": res.status,
                    "cfo.policy_level": final,
                    "cfo.simulated": (
                        "yes" if any(x.executed_simulated for x in decs) else "no"
                    ),
                    "cfo.actions": ",".join(dict.fromkeys(x.action_type for x in decs))
                    or None,
                }
            )
            sp.set_outputs(res)
        except Exception:  # noqa: BLE001 - tracing must never break event handling
            log.warning("could not tag the MLflow trace", exc_info=True)
        return res

    def _result(self, config: dict[str, Any]) -> HandleResult:
        snap = self._graph.get_state(config)
        v = snap.values
        if snap.next:
            status = "awaiting_approval"
        elif v.get("steps") and all(s.startswith("ignored") for s in v["steps"]):
            status = "ignored"
        else:
            status = "done"
        return HandleResult(
            status=status,
            decision_ids=v.get("decision_ids", []),
            approval_id=(v.get("approval") or {}).get("approval_id"),
            next_reminder_hours=v.get("next_reminder_hours"),
            steps=v.get("steps", []),
        )


def build_agent(
    tools: ToolBox,
    repo_factory: Callable[[], Repository],
    llm: LLM,
    notifier: Notifier,
    config: AgentConfig | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Agent:
    """`checkpointer`: `postgres_checkpointer(...)` in production, default in-memory (tests, single process)."""
    return Agent(
        Deps(tools, repo_factory, llm, notifier, config or AgentConfig()), checkpointer
    )


def postgres_checkpointer(
    database_url: str, schema: str | None = None
) -> BaseCheckpointSaver:
    """LangGraph Postgres saver so pending approvals survive a restart. `schema` pins the search_path."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row

    kwargs: dict[str, Any] = {"autocommit": True, "row_factory": dict_row}
    if schema:
        kwargs["options"] = f"-csearch_path={schema}"
    saver = PostgresSaver(psycopg.connect(database_url, **kwargs))
    saver.setup()
    return saver
