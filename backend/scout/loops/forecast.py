"""Forecast loop: the CEO's annual revenue expectation, checked against all company data, turned into team work.

Jobs
- `forecast_review` (daily from the scheduler when the plan is enabled, on demand, and after the CEO saves the plan):
  reads the bank ledger, supplier invoices, email and Slack, builds a realistic 12-month forecast next to the target
  path, finds the weakest points and opens one `forecast_action` task per weak point for its team owner.
- `forecast_drift` (from `DriftWatcher`): something happened that the plan forbids (a hire while the CEO said no
  hiring, a new recurring cost above the limit). Scout opens an urgent task, DMs and emails the CEO, asks the person
  responsible why, and opens alignment tasks. Scout never reverses or blocks the action itself.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from scout import forecast_plan as fp
from scout.jobs import JobQueue
from scout.loops.context import JobContext
from scout.models import Job, Task

log = logging.getLogger(__name__)
LOOP = "forecast"
LOCAL_TZ = ZoneInfo("Europe/Berlin")
HIRING_WORDS = ("hire", "hiring", "new employee", "recruit", "headcount", "new position", "job offer")


def _local_day(ctx: JobContext) -> date:
    return ctx.now().astimezone(LOCAL_TZ).date()


def _team_owner(conn: Any, team_id: str | None) -> str | None:
    if not team_id:
        return None
    row = conn.execute("SELECT owner_id FROM teams WHERE id = %s", (team_id,)).fetchone()
    return row["owner_id"] if row else None


# -- review ------------------------------------------------------------------------------------------------------
def _sources(ctx: JobContext, conn: Any, today: date) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """What Scout checked in each system, and the numbers it found there."""
    since = datetime.combine(today - timedelta(days=30), datetime.min.time(), tzinfo=timezone.utc)
    facts: dict[str, Any] = {}
    out = []
    txn = conn.execute("SELECT count(*) AS n FROM transactions WHERE booking_date >= %s",
                       (today - timedelta(days=365),)).fetchone()["n"]
    und = conn.execute("SELECT count(*) AS n, coalesce(sum(-amount), 0) AS total FROM transactions "
                       "WHERE amount < 0 AND NOT documented AND booking_date >= %s",
                       (today - timedelta(days=90),)).fetchone()
    facts["undocumented"] = (und["n"], fp.money(und["total"]))
    out.append({"name": "bank", "status": "ok" if txn else "unavailable", "count": txn,
                "note": f"{txn} bank transactions in the last 12 months; {und['n']} recent payments without a document"})
    inv = conn.execute("SELECT count(*) AS n, coalesce(sum(total) FILTER (WHERE transaction_id IS NULL "
                       "AND issue_date >= %s), 0) AS open_total, count(*) FILTER (WHERE transaction_id IS NULL "
                       "AND issue_date >= %s) AS open_n FROM invoices", (today - timedelta(days=90),) * 2).fetchone()
    facts["unmatched_invoices"] = (inv["open_n"], fp.money(inv["open_total"]))
    out.append({"name": "invoices", "status": "ok" if inv["n"] else "unavailable", "count": inv["n"],
                "note": f"{inv['n']} supplier invoices; {inv['open_n']} recent ones not matched to a payment"})
    try:
        mail = ctx.connectors.mail
        if mail.available():
            hits = mail.search(["invoice", "payment", "order", "contract", "price"], since)
            out.append({"name": "email", "status": "ok", "count": len(hits),
                        "note": f"{len(hits)} emails about invoices, payments, orders or prices in the last 30 days"})
        else:
            out.append({"name": "email", "status": "unavailable", "count": 0, "note": "No mailbox connected"})
    except Exception:  # noqa: BLE001 - one source down never stops the review
        log.warning("forecast review: email unavailable", exc_info=True)
        out.append({"name": "email", "status": "unavailable", "count": 0, "note": "Mailbox could not be read"})
    try:
        hits = ctx.connectors.chat.search(since=since)
        out.append({"name": "slack", "status": "ok", "count": len(hits),
                    "note": f"{len(hits)} finance messages on Slack in the last 30 days"})
    except Exception:  # noqa: BLE001
        log.warning("forecast review: Slack unavailable", exc_info=True)
        out.append({"name": "slack", "status": "unavailable", "count": 0, "note": "Slack could not be read"})
    return out, facts


def review(ctx: JobContext, job: Job) -> Task | None:
    today = date.fromisoformat(job.payload.get("day") or _local_day(ctx).isoformat())
    key = job.dedupe_key or f"forecast:{today}"
    existing = ctx.recorder.by_key(key)
    if existing and existing.status == "done":
        return existing
    with ctx.conn_factory() as conn:
        plan = fp.load(conn)
        if plan is not None and not plan.enabled and job.payload.get("scheduled"):
            return None  # paused by the CEO: manual runs still work
        rows = conn.execute("SELECT booking_date, amount, category, team_id FROM transactions "
                            "WHERE booking_date >= %s AND booking_date < %s AND currency = 'EUR'",
                            (fp.month_start(today, -12), fp.month_start(today))).fetchall()
        sources, facts = _sources(ctx, conn, today)
    monthly = fp.monthly_revenue(rows, today)
    history = list(monthly.values())
    trailing = fp.money(sum(history, Decimal(0)))
    target_source = "ceo" if plan else "default"
    if plan is None:
        if trailing <= 0:
            return None  # no revenue history and no target: nothing to plan yet
        plan = fp.Plan(fp.default_target(trailing))
    target = plan.annual_revenue_target
    task = ctx.recorder.open(key, LOOP, f"Revenue forecast · {today}", owner_id=ctx.settings.ceo_id or None,
                             category="forecast_review", level="AUTO")
    for s in sources:
        ctx.recorder.step(task.id, "checked", s["note"], source=s["name"], result_count=s["count"])
    forecast = fp.project(history)
    realistic = fp.money(sum(forecast, Decimal(0)))
    path = fp.target_path(history, target)
    gap = fp.money(realistic - target)
    ctx.recorder.step(task.id, "forecast", f"Realistic next 12 months: €{realistic:,.0f} vs expectation "
                                           f"€{target:,.0f} ({'+' if gap >= 0 else '−'}€{abs(gap):,.0f})",
                      source="forecast", detail={"trend": str(fp.trend(history)), "run_rate": str(fp.money(fp.run_rate(history)))})
    weak = fp.weak_points(rows, today, target=target, realistic=realistic,
                          undocumented=facts["undocumented"], unmatched_invoices=facts["unmatched_invoices"])
    actions = []
    with ctx.conn_factory() as conn:
        for w in weak:
            owner = _team_owner(conn, w.get("team_id")) or (
                ctx.settings.document_owner_id if w["kind"] in ("documents", "invoices") else ctx.settings.ceo_id) or None
            title = fp.action_for(w, plan)
            # One open action per weak point and month, so the daily review never piles up duplicates.
            act = ctx.recorder.open(f"forecast-action:{w['kind']}:{w.get('category') or ''}:{today:%Y-%m}", LOOP, title,
                                    owner_id=owner, category="forecast_action", status="todo", level="NOTIFY",
                                    amount=w["impact_annual"])
            if not ctx.recorder.steps(act.id):
                ctx.recorder.step(act.id, "planned", w["detail"], source="forecast", detail={"weak_point": w["area"]})
                if owner:
                    ctx.notifier.dm(owner, f"Forecast plan: {title}. Why: {w['detail']}", "NOTIFY", task_id=act.id)
            actions.append({"task_id": act.id, "title": title, "owner_id": owner})
    if weak:
        ctx.recorder.step(task.id, "weak_points", "Weakest point: " + weak[0]["area"] + ". " + weak[0]["detail"],
                          source="forecast", detail={"weak_points": [{**w, "impact_annual": str(w["impact_annual"])} for w in weak]})
    past = [{"month": m.isoformat(), "actual": str(fp.money(v)), "target": None, "forecast": None} for m, v in monthly.items()]
    future = [{"month": fp.month_start(today, i).isoformat(), "actual": None, "target": str(path[i]),
               "forecast": str(forecast[i])} for i in range(12)]
    report = {"kind": "forecast_plan", "as_of": today.isoformat(), "currency": "EUR",
              "trailing_revenue": str(trailing), "target_annual": str(fp.money(target)), "target_source": target_source,
              "realistic_annual": str(realistic), "gap": str(gap), "months": past + future,
              "weak_points": [{"area": w["area"], "detail": w["detail"], "impact_annual": str(w["impact_annual"])} for w in weak],
              "actions": actions, "sources": sources,
              "constraints": {"no_hiring": plan.no_hiring, "max_new_monthly_cost":
                              None if plan.max_new_monthly_cost is None else str(fp.money(plan.max_new_monthly_cost))},
              "guidance": plan.guidance}
    ctx.recorder.update(task.id, evidence=[report])
    lines = [f"Expectation €{target:,.0f} for the next 12 months ({'set by the CEO' if target_source == 'ceo' else '150% of the last 12 months'}).",
             f"Realistic forecast €{realistic:,.0f}: {'on track' if gap >= 0 else f'€{-gap:,.0f} short'}."]
    lines += [f"• {w['area']}: {w['detail']}" for w in weak]
    if actions:
        lines.append(f"Opened {len(actions)} tasks for the team to close the gap.")
    return ctx.recorder.set_status(task.id, "done", outcome="\n".join(lines), resolved_via="forecast", level="AUTO")


# -- drift -------------------------------------------------------------------------------------------------------
def _is_hiring(title: str, dedupe_key: str) -> bool:
    text = title.casefold()
    return dedupe_key.startswith("web-hiring:") or any(w in text for w in HIRING_WORDS)


class DriftWatcher:
    """Looks at every new task and bank payment and queues a `forecast_drift` job when one breaks the plan."""

    NAME = "forecast_drift"

    def __init__(self, conn_factory: Any, jobs: JobQueue):
        self._cf, self._jobs = conn_factory, jobs

    def poll(self) -> int:
        with self._cf() as conn:
            plan = fp.load(conn)
            state = conn.execute("SELECT cursor FROM watcher_state WHERE name = %s", (self.NAME,)).fetchone()
            now = conn.execute("SELECT now() AS t").fetchone()["t"]
            if plan is None or not plan.enabled:
                return 0
            if state is None:  # first look: only what happens from now on counts, history is not drift
                self._save(conn, {"tasks_since": now.isoformat(), "txn_ids": []})
                conn.commit()
                return 0
            cur = dict(state["cursor"])
            since = datetime.fromisoformat(cur["tasks_since"])
            found: list[tuple[str, dict[str, Any]]] = []
            tasks = conn.execute("SELECT id, dedupe_key, title, loop, owner_id, requested_by, amount, created_at "
                                 "FROM agent_tasks WHERE created_at > %s AND loop <> 'forecast' ORDER BY created_at",
                                 (since,)).fetchall()
            for t in tasks:
                if plan.no_hiring and _is_hiring(t["title"], t["dedupe_key"]):
                    found.append((f"drift:task:{t['id']}", {"rule": "no_hiring", "task_id": t["id"]}))
            seen = set(cur.get("txn_ids", []))
            txns = conn.execute(
                "SELECT t.id, t.counterparty, t.amount, t.category, t.team_id, NOT EXISTS (SELECT 1 FROM transactions p "
                "WHERE p.counterparty = t.counterparty AND p.amount < 0 AND p.booking_date < t.booking_date "
                "AND p.booking_date >= t.booking_date - 365) AS new_payee FROM transactions t "
                "WHERE t.amount < 0 AND t.booking_date >= (now() AT TIME ZONE 'Europe/Berlin')::date - 7",
            ).fetchall()
            for t in txns:
                if t["id"] in seen or not t["new_payee"]:
                    continue
                seen.add(t["id"])
                if plan.no_hiring and t["category"] == "payroll":
                    found.append((f"drift:txn:{t['id']}", {"rule": "no_hiring", "txn_id": t["id"]}))
                elif plan.max_new_monthly_cost is not None and -Decimal(str(t["amount"])) > plan.max_new_monthly_cost:
                    found.append((f"drift:txn:{t['id']}", {"rule": "max_new_monthly_cost", "txn_id": t["id"]}))
            last = tasks[-1]["created_at"].isoformat() if tasks else cur["tasks_since"]
            self._save(conn, {"tasks_since": last, "txn_ids": sorted(seen)[-500:]})
            conn.commit()
        return sum(1 for key, payload in found if self._jobs.enqueue("forecast_drift", payload, key))

    @staticmethod
    def _save(conn: Any, cursor: dict[str, Any]) -> None:
        conn.execute("INSERT INTO watcher_state (name, cursor) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET "
                     "cursor = EXCLUDED.cursor, updated_at = now()", (DriftWatcher.NAME, Jsonb(cursor)))


def _who_and_what(ctx: JobContext, conn: Any, payload: dict[str, Any], plan: fp.Plan) -> dict[str, Any]:
    if payload.get("task_id"):
        t = ctx.recorder.get(payload["task_id"])
        actor = t.requested_by if t.requested_by not in ("agent", "", None) else t.owner_id
        cost = -t.amount if t.amount is not None and t.amount < 0 else None
        return {"actor": actor, "what": f"“{t.title}” ({t.id})", "ref": t.id, "monthly_cost": cost}
    t = conn.execute("SELECT * FROM transactions WHERE id = %s", (payload["txn_id"],)).fetchone()
    return {"actor": _team_owner(conn, t["team_id"]), "ref": t["id"], "monthly_cost": -Decimal(str(t["amount"])),
            "what": f"a new {t['category'] or 'payment'} to {t['counterparty']} of €{-Decimal(str(t['amount'])):,.2f} ({t['id']})"}


def drift(ctx: JobContext, job: Job) -> Task | None:
    key = job.dedupe_key
    existing = ctx.recorder.by_key(key)
    if existing:
        return existing
    with ctx.conn_factory() as conn:
        plan = fp.load(conn)
        if plan is None or not plan.enabled:
            return None
        info = _who_and_what(ctx, conn, job.payload, plan)
    email = None
    with ctx.conn_factory() as conn:
        email = fp.ceo_email(conn, ctx.settings)
    if job.payload["rule"] == "no_hiring":
        rule = "the plan says no new hires"
    else:
        rule = f"the plan caps new monthly costs at €{plan.max_new_monthly_cost:,.0f}"
    cost = info["monthly_cost"]
    hit = f" It adds about €{cost:,.0f} a month (≈ €{cost * 12:,.0f} a year) the forecast does not include." if cost else ""
    actor, ceo = info["actor"], ctx.settings.ceo_id
    who = ctx.hr.name(actor) if actor else "someone"
    task = ctx.recorder.open(key, LOOP, f"URGENT: {info['what']} breaks the forecast plan", owner_id=ceo or None,
                             category="drift", status="escalated", level="NOTIFY", amount=-cost if cost else None)
    ctx.recorder.step(task.id, "detected", f"{info['what']} contradicts the CEO's plan: {rule}.{hit}",
                      source="forecast", detail={"rule": job.payload["rule"], "ref": info["ref"], "actor": actor})
    question = (f"URGENT from Scout: {info['what']} goes against the forecast plan ({rule}).{hit} "
                "Why was this done? Please reply here so the CEO can decide to approve it and re-plan, or reverse it.")
    if actor and actor != ceo:
        if ctx.notifier.dm(actor, question, "NOTIFY", task_id=task.id):
            ctx.recorder.step(task.id, "asked", f"Asked {who} why", source="slack:dm")
    ceo_text = (f"🚨 URGENT: {info['what']} breaks your forecast plan — {rule}.{hit} "
                f"Requested by {who}. I asked them why and opened tasks to realign. Review: {task.id}.")
    if ceo and ctx.notifier.dm(ceo, ceo_text, "NOTIFY", task_id=task.id):
        ctx.recorder.step(task.id, "alerted", f"Alerted {ctx.hr.name(ceo)} on Slack", source="slack:dm")
    if email:
        body = (f"{ceo_text}\n\nWhat happened: {info['what']}\nRule in your plan: {rule}\n"
                f"Responsible: {who}\n\nNext steps Scout opened:\n- {who}: explain the decision and pause it until "
                f"you decide\n- You: approve and re-run the forecast, or reverse it\n\nGuidance on file: "
                f"{plan.guidance or '—'}\n")
        if ctx.mailer.send(email, f"URGENT: forecast plan broken — {info['what']}", body):
            ctx.recorder.step(task.id, "emailed", f"Emailed the CEO at {email}", source="email")
    else:
        ctx.recorder.step(task.id, "email_skipped", "No CEO email address on file; Slack only", source="email")
    ctx.audit.record("agent", "forecast_drift_alert", f"slack:{ceo}", info["ref"], task_id=task.id,
                     reason=job.payload["rule"])
    align = [(actor, f"Explain and pause {info['what']} until the CEO decides"),
             (ceo, f"Decide on {info['what']}: approve and re-plan, or reverse it")]
    opened = []
    for i, (owner, title) in enumerate(align):
        t = ctx.recorder.open(f"{key}:align:{i}", LOOP, title, owner_id=owner or ceo or None,
                              category="forecast_action", status="todo", level="NOTIFY")
        ctx.recorder.step(t.id, "planned", f"Raised by drift alert {task.id}: {rule}.", source="forecast")
        opened.append(t.id)
    ctx.recorder.step(task.id, "aligned", f"Opened alignment tasks {', '.join(opened)}", source="forecast",
                      detail={"tasks": opened})
    ctx.jobs.enqueue("forecast_review", {"day": _local_day(ctx).isoformat(), "reason": key},
                     f"forecast:{_local_day(ctx)}:{key}")
    return ctx.recorder.get(task.id)
