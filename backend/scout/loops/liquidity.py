"""Liquidity: "what if I buy an oven for €30k in March?" and the weekly liquidity review.

The forecast (`cfo.forecast.liquidity`) finds gaps and proposes a credit line (or an investment of a surplus).
Strategic actions always need the CEO: the task stops at a LangGraph `interrupt()` (thread id = task id, Postgres
checkpointer in the worker) until the CEO clicks Approve/Reject in Slack or the dashboard; `resume` continues it.
Execution is simulated: Scout never moves money (the outcome says SIMULATED).
"""

from __future__ import annotations

import logging
import re
import secrets
from calendar import month_name
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from scout.loops.context import Button, JobContext, eur
from scout.models import Approval, Job, Task

log = logging.getLogger(__name__)
LOOP = "liquidity"


class State(TypedDict, total=False):
    task_id: str
    approval_id: str
    answer: dict[str, Any]


# -- forecasting ------------------------------------------------------------------------------------------
def _default_forecaster(ctx: JobContext, outflows: list[dict[str, Any]]):  # noqa: ANN202
    from cfo.forecast.liquidity import DEFAULT_HORIZON_DAYS, review_liquidity

    today = ctx.now().date()
    days = max([DEFAULT_HORIZON_DAYS, *[(o["on"] - today).days + 45 for o in outflows]])
    conn = ctx.conn_factory()
    try:
        return review_liquidity(outflows, conn=conn, days=days)
    finally:
        conn.close()


def parse_month(text: str | None, today: date) -> date:
    """"2027-03", "March", "march 2027", "" -> the first day of that month (names: the next one from today)."""
    t = (text or "").strip().casefold()
    this_month = today.replace(day=1)

    def upcoming(d: date) -> date:  # a past month (the model's stale "2024-03") means the next one ahead
        while d < this_month:
            d = d.replace(year=d.year + 1)
        return d

    try:
        y, m = t.split("-")[:2]
        return upcoming(date(int(y), int(m), 1))
    except ValueError:
        pass
    for i in range(1, 13):
        if month_name[i].casefold() in t or (len(t) >= 3 and month_name[i].casefold().startswith(t[:3])):
            year = next((int(w) for w in t.split() if w.isdigit() and len(w) == 4), today.year)
            return upcoming(date(year, i, 1))
    return date(this_month.year + (this_month.month == 12), this_month.month % 12 + 1, 1)


def parse_eur(value: Any) -> Decimal | None:
    s = str(value or "").lower()
    for junk in ("€", "eur", "$", "usd", ",", " "):
        s = s.replace(junk, "")
    mult = Decimal(1000) if s.endswith("k") else Decimal(1)
    try:
        v = Decimal(s.rstrip("k")) * mult
    except InvalidOperation:
        return None
    return v if v > 0 else None


INFLOW_WORDS = re.compile(r"\b(client|customer|income|revenue|receiv\w*|incoming|inflow|pays? us|paid (to )?us|"
                          r"refund\w*|deposit|grant|subsidy|earn\w*|sales?|sell\w*|invoice paid|win|won)\b", re.I)


def direction_of(direction: Any, what: str | None) -> str:
    """"in" (money arriving: a client paying) or "out" (a purchase). An explicit "in"/"out" wins; else the words."""
    d = str(direction or "").strip().casefold()
    if d in ("in", "inflow", "income", "incoming", "receive", "+"):
        return "in"
    if d in ("out", "outflow", "purchase", "expense", "spend", "pay", "-"):
        return "out"
    return "in" if INFLOW_WORDS.search(what or "") else "out"


def money(v: Any) -> str:
    """A balance with its sign: "-€475,802.29" (`eur` drops the sign, which is right for amounts, not balances)."""
    d = Decimal(str(v))
    return ("-" if d < 0 else "") + eur(d)


def _lowest(fc: Any) -> tuple[Decimal, date] | None:
    if fc is None or "balance" not in fc or not len(fc):
        return None
    row = fc.loc[fc["balance"].idxmin()]
    day = row.name.date() if hasattr(row.name, "date") else row.get("date")
    return Decimal(str(round(float(row["balance"]), 2))), day


# -- the approval graph -----------------------------------------------------------------------------------
def _graph(ctx: JobContext):  # noqa: ANN202
    if ctx.checkpointer is None:
        ctx.checkpointer = InMemorySaver()

    def wait(state: State) -> State:
        answer = interrupt({"approval_id": state["approval_id"]})  # nothing before this line: it re-runs on resume
        return {"answer": answer}

    def act(state: State) -> State:
        _after_answer(ctx, state["task_id"], state["approval_id"], bool(state["answer"]["approved"]),
                      str(state["answer"]["by"]))
        return {}

    g = StateGraph(State)
    g.add_node("wait", wait)
    g.add_node("act", act)
    g.add_edge(START, "wait")
    g.add_edge("wait", "act")
    g.add_edge("act", END)
    return g.compile(checkpointer=ctx.checkpointer)


def _config(task_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": task_id}}


def _get_approval(ctx: JobContext, approval_id: str) -> Approval | None:
    with ctx.conn_factory() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE id = %s", (approval_id,)).fetchone()
    return Approval(**row) if row else None


def _send_card(ctx: JobContext, task_id: str, apr_id: str, approver: str, question: str) -> str | None:
    """The approval card (Approve / Reject buttons) in the approver's DM; its ref is stored on the approval."""
    ref = ctx.notifier.dm(approver, question, "APPROVAL", task_id=task_id,
                          buttons=[Button("scout_approve", "Approve", apr_id), Button("scout_reject", "Reject", apr_id)])
    with ctx.conn_factory() as conn:
        conn.execute("UPDATE approvals SET slack_ref = %s WHERE id = %s", (ref, apr_id))
        conn.commit()
    return ref


def reassign(ctx: JobContext, approval_id: str, to: str, by: str, chat_task: str | None = None) -> Task:
    """Hand a pending approval to someone else: they get the card, the old card says so, the chat hears it.
    Only the current approver or the owner (CEO) may do it; anyone else raises PermissionError (audited)."""
    apr = _get_approval(ctx, approval_id)
    if apr is None:
        raise KeyError(approval_id)
    if apr.status != "pending":
        raise ValueError(f"{approval_id} is already {apr.status}")
    if by not in (apr.approver_id, ctx.settings.ceo_id):
        ctx.audit.record("agent", "reassign_approval", f"approval:{approval_id}", f"to {to}", "DENY",
                         task_id=apr.task_id, requester=by, policy="approvals: approver or owner")
        raise PermissionError(f"{by} may not hand over {approval_id}")
    old_ref = apr.slack_ref
    with ctx.conn_factory() as conn:
        conn.execute("UPDATE approvals SET approver_id = %s WHERE id = %s", (to, approval_id))
        conn.commit()
    who, new = ctx.hr.name(by), ctx.hr.name(to)
    ref = _send_card(ctx, apr.task_id, approval_id, to, f"{who} asked you to decide this instead.\n\n{apr.question}")
    if old_ref:
        ctx.notifier.update(old_ref, f"{apr.question}\n\n↪ Handed to {new} by {who}", "APPROVAL")
    ctx.recorder.step(apr.task_id, "reassigned", f"{who} asked {new} to approve instead", source="approval",
                      detail={"approval_id": approval_id, "to": to, **({"chat_task": chat_task} if chat_task else {})})
    ctx.recorder.update(apr.task_id, owner_id=to, slack_ref=ref)
    ctx.audit.record(by, "reassign_approval", f"approval:{approval_id}", f"to {to}", task_id=apr.task_id,
                     requester=by)
    _chat_followup(ctx, apr.task_id, f"Update on {apr.task_id}: {new} now decides the approval (asked by {who}); "
                                     "I sent them the Approve / Reject card.")
    return ctx.recorder.get(apr.task_id)


def _ask_approval(ctx: JobContext, task: Task, question: str) -> Approval:
    approver = ctx.settings.ceo_id
    apr_id = "apr_" + secrets.token_hex(5)
    with ctx.conn_factory() as conn:
        conn.execute("INSERT INTO approvals (id, task_id, approver_id, question) VALUES (%s, %s, %s, %s)",
                     (apr_id, task.id, approver, question))
        conn.commit()
    ref = _send_card(ctx, task.id, apr_id, approver, question)
    ctx.recorder.step(task.id, "asked_approval", f"Asked {ctx.hr.name(approver)} to approve", source="slack:dm",
                      detail={"approval_id": apr_id})
    ctx.recorder.update(task.id, status="waiting", owner_id=approver, approval_id=apr_id, level="APPROVAL",
                        slack_ref=ref)
    _graph(ctx).invoke({"task_id": task.id, "approval_id": apr_id}, _config(task.id))  # stops at interrupt()
    return _get_approval(ctx, apr_id)


def _after_answer(ctx: JobContext, task_id: str, approval_id: str, approved: bool, by: str) -> None:
    task = ctx.recorder.get(task_id)
    proposal = next((s.detail for s in reversed(ctx.recorder.steps(task_id)) if s.kind == "proposed"), {})
    who = ctx.hr.name(by)
    if approved:
        ctx.recorder.step(task_id, "approved", f"{who} approved", source="approval")
        what = proposal.get("summary") or task.title
        outcome = f"Resolved · {what} (SIMULATED: no money moved, the bank request is prepared for {who})"
        ctx.recorder.step(task_id, "resolved", outcome)
        ctx.recorder.set_status(task_id, "done", outcome=outcome, resolved_via="approval")
    else:
        ctx.recorder.step(task_id, "rejected", f"{who} rejected", source="approval")
        outcome = f"Closed · {who} rejected the proposal"
        ctx.recorder.step(task_id, "resolved", outcome)
        ctx.recorder.set_status(task_id, "cancelled", outcome=outcome, resolved_via="approval")
    apr = _get_approval(ctx, approval_id)
    if apr and apr.slack_ref:
        ctx.notifier.update(apr.slack_ref, f"{apr.question}\n\n{'✅ Approved' if approved else '❌ Rejected'} by {who}",
                            "APPROVAL")
    _tell_requester(ctx, task_id, by)


def _tell_requester(ctx: JobContext, task_id: str, by: str) -> None:
    """The person who asked for the what-if hears how it ended: their "Waiting for approval" message is replaced,
    or, without one, they get a DM. The approver already sees it on the approval message."""
    task = ctx.recorder.get(task_id)
    steps = ctx.recorder.steps(task_id)
    verdict = "approved" if task.status == "done" else "rejected"
    _chat_followup(ctx, task_id, f"Update on {task.id}: {ctx.hr.name(by)} {verdict} it. {task.outcome}")
    steps = ctx.recorder.steps(task_id)
    first = steps[0].detail if steps else {}
    status_ref, requester = (first or {}).get("status_ref"), task.requested_by
    lines = "\n".join(f"• {s.text}" for s in steps[1:][-6:])
    text = f"{task.outcome} ({task.id})\n{lines}"
    if status_ref:
        ctx.notifier.update(status_ref, text, "NOTIFY")
    elif requester and requester not in ("agent", by):
        ctx.notifier.dm(requester, text, "NOTIFY", task_id=task.id)


def resume(ctx: JobContext, approval_id: str, approved: bool, by: str) -> Task:
    """The approver clicked. Raises PermissionError for anyone else; a second click changes nothing."""
    apr = _get_approval(ctx, approval_id)
    if apr is None:
        raise KeyError(approval_id)
    if apr.status != "pending":
        return ctx.recorder.get(apr.task_id)
    if by != apr.approver_id:
        ctx.audit.record("agent", "approve", f"approval:{approval_id}", f"click by {by}", "DENY", task_id=apr.task_id,
                         requester=by, policy="approvals: approver-only")
        raise PermissionError(f"{by} is not the approver of {approval_id}")
    with ctx.conn_factory() as conn:
        row = conn.execute("UPDATE approvals SET status = %s, answered_by = %s, answered_at = now() "
                           "WHERE id = %s AND status = 'pending' RETURNING id",
                           ("approved" if approved else "rejected", by, approval_id)).fetchone()
        conn.commit()
    if row is None:  # someone else answered a moment ago
        return ctx.recorder.get(apr.task_id)
    ctx.audit.record(by, "approve" if approved else "reject", f"approval:{approval_id}", apr.question[:200],
                     task_id=apr.task_id, requester=by, role="ceo")
    _graph(ctx).invoke(Command(resume={"approved": approved, "by": by}), _config(apr.task_id))
    return ctx.recorder.get(apr.task_id)


# -- jobs -------------------------------------------------------------------------------------------------
def _review_steps(ctx: JobContext, task: Task, review: Any, what: str | None, base: Any = None,
                  inflow: bool = False) -> Any:
    low = _lowest(review.forecast)
    if low is not None:
        without = _lowest(base.forecast) if base is not None else None
        compare = (f"; without it: {money(without[0])} on {without[1]:%b %d}" if without and what else "")
        ctx.recorder.step(task.id, "forecast",
                          f"Forecast {'with ' + what if what else 'for the next months'}: lowest balance "
                          f"{money(low[0])} on {low[1]:%b %d}{compare} (safety buffer {eur(review.buffer)})",
                          source="forecast")
    for gap in review.gaps[:3]:
        ctx.recorder.step(task.id, "gap", f"Gap of {eur(gap.shortfall)} below the buffer around {gap.lowest_date:%b %d}",
                          source="forecast")
    if review.gaps and base is not None and what:
        if base.gaps and inflow:
            ctx.recorder.step(task.id, "note", "The gap is there even without this money; it doesn't close it.",
                              source="forecast")
        elif base.gaps:
            ctx.recorder.step(task.id, "note", "The gap is there even without this purchase.", source="forecast")
        else:
            ctx.recorder.step(task.id, "note", "Without this purchase the balance stays above the buffer: the "
                              "purchase causes the gap.", source="forecast")
    return review.proposals[0] if review.proposals else None


def _propose(ctx: JobContext, task: Task, p: Any) -> None:
    summary = f"{p.instrument} of {eur(p.amount)} for {p.period_start:%b %d}–{p.period_end:%b %d}"
    ctx.recorder.step(task.id, "proposed", f"Proposed a {summary}", source="forecast",
                      detail={"summary": summary[0].upper() + summary[1:], "action_type": p.action_type,
                              "amount": str(p.amount), "reason": p.reason})
    question = (f"{p.reason}\n\nI propose a *{summary}*. Approve? (Simulated: Scout prepares the request, no money "
                "moves.)")
    _ask_approval(ctx, task, question)


def _chat_followup(ctx: JobContext, task_id: str, text: str) -> None:
    """A what-if reports back into every chat that started or handed it on: a `followup` step on each chat's task,
    which the dashboard chat shows as a new message from Scout."""
    chats: list[str] = []
    for s in ctx.recorder.steps(task_id):
        c = (s.detail or {}).get("chat_task")
        if c and c not in chats:
            chats.append(c)
    for c in chats:
        ctx.recorder.step(c, "followup", text, source="scout", detail={"task": task_id})


def _status_done(ctx: JobContext, p: dict[str, Any], task_id: str) -> Task:
    """Replace the requester's "Working on it…" message with where the what-if ended (and tell the chat)."""
    task = ctx.recorder.get(task_id)
    steps = ctx.recorder.steps(task_id)
    if task.status == "waiting":
        proposed = next((s.text for s in reversed(steps) if s.kind == "proposed"), "")
        forecast = next((s.text for s in steps if s.kind == "forecast"), "")
        note = next((" " + s.text for s in steps if s.kind == "note"), "")
        _chat_followup(ctx, task_id, f"{task.title} ({task.id}): {forecast}.{note} {proposed}; I asked "
                                     f"{ctx.hr.name(task.owner_id)} for approval and will tell you the answer here.")
    else:
        _chat_followup(ctx, task_id, f"{task.title} ({task.id}): {task.outcome or steps[-1].text}")
    if p.get("status_ref"):
        steps = ctx.recorder.steps(task_id)
        lines = "\n".join(f"• {s.text}" for s in steps[1:][-6:])
        head = task.outcome or (f"Waiting for {ctx.hr.name(task.owner_id)}'s approval" if task.status == "waiting"
                                else steps[-1].text)
        ctx.notifier.update(p["status_ref"], f"{head} ({task.id})\n{lines}", task.level or "NOTIFY")
    return task


def on_what_if(ctx: JobContext, job: Job) -> Task:
    p = job.payload
    amount = parse_eur(p.get("amount"))
    month = parse_month(p.get("month"), ctx.now().date())
    what = (p.get("what") or "").strip()
    inflow = direction_of(p.get("direction"), what) == "in"
    what = what or ("the payment" if inflow else "the purchase")
    sign = "+" if inflow else ""
    title = f"What if: {what} {sign + eur(amount) if amount else ''} in {month:%B %Y}".replace("  ", " ")
    task = ctx.recorder.open(f"whatif:{job.dedupe_key}", LOOP, title, requested_by=p.get("from_id") or "agent",
                             amount=(amount if inflow else -amount) if amount else None, simulated=True)
    if ctx.recorder.steps(task.id):
        return task
    if p.get("status_ref"):
        ctx.notifier.track(task.id, p["status_ref"])
    verb = "we receive" if inflow else "we buy"
    ctx.recorder.step(task.id, "picked_up", f"{ctx.hr.name(p.get('from_id'))} asked: what if {verb} {what} for "
                                           f"{eur(amount) if amount else '?'} in {month:%B}", source="slack:dm",
                      detail={k: p[k] for k in ("status_ref", "chat_task") if p.get(k)} or None)
    if amount is None:
        outcome = "Closed · I need the amount to run the forecast"
        ctx.recorder.step(task.id, "resolved", outcome)
        ctx.recorder.set_status(task.id, "done", outcome=outcome, resolved_via="forecast", level="AUTO")
        return _status_done(ctx, p, task.id)
    forecaster = ctx.forecaster or _default_forecaster
    try:
        review = forecaster(ctx, [{"on": month, "amount": amount, "label": what,
                                   "direction": "in" if inflow else "out"}])
    except Exception as e:  # noqa: BLE001
        log.exception("forecast failed")
        ctx.recorder.step(task.id, "error", f"Couldn't run the forecast: {type(e).__name__}")
        ctx.recorder.set_status(task.id, "escalated", owner_id=ctx.settings.ceo_id)
        return _status_done(ctx, p, task.id)
    try:
        base = forecaster(ctx, [])
    except Exception:  # noqa: BLE001 — the comparison is a nicety; the scenario stands on its own
        log.exception("base forecast failed")
        base = None
    label = f"{what} ({sign}{eur(amount)} in {month:%B})"
    proposal = _review_steps(ctx, task, review, label, base, inflow)
    if proposal is None or not review.gaps:
        low = _lowest(review.forecast)
        lowest = f" (lowest {money(low[0])} on {low[1]:%b %d})" if low else ""
        outcome = (f"Resolved · with this money the balance stays above the {eur(review.buffer)} buffer{lowest}"
                   if inflow else
                   f"Resolved · affordable: the balance stays above the {eur(review.buffer)} buffer{lowest}")
        ctx.recorder.step(task.id, "resolved", outcome, source="forecast")
        ctx.recorder.set_status(task.id, "done", outcome=outcome, resolved_via="forecast", level="AUTO")
        return _status_done(ctx, p, task.id)
    _propose(ctx, task, proposal)
    return _status_done(ctx, p, task.id)


def on_review(ctx: JobContext, job: Job) -> Task | None:
    forecaster = ctx.forecaster or _default_forecaster
    review = forecaster(ctx, [])
    if not review.proposals:
        return None
    p = review.proposals[0]
    key = f"liquidity:{p.action_type}:{p.month}"
    if ctx.recorder.by_key(key):
        return ctx.recorder.by_key(key)
    title = ("Cash gap in " if p.action_type == "request_credit_line" else "Idle cash in ") + \
        date.fromisoformat(p.month + "-01").strftime("%B")
    task = ctx.recorder.open(key, LOOP, title, amount=p.amount, simulated=True)
    ctx.recorder.step(task.id, "picked_up", "Scout ran the weekly liquidity review", source="forecast")
    _review_steps(ctx, task, review, None)
    _propose(ctx, task, p)
    return ctx.recorder.get(task.id)


HANDLERS = {"what_if": on_what_if, "liquidity_review": on_review}


def run(ctx: JobContext, job: Job) -> Task | None:
    return HANDLERS[job.kind](ctx, job)
