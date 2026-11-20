"""Cost control: a cost that grew without the business metric behind it (deck: "cloud +50% while orders +3%").

Job `cost_check` (scheduler, daily; payload `{}`) looks at the newest month of every cost-vs-metric pair and opens
one task per anomaly (dedupe `metric:<kind>:<month>`), so a re-run never alerts twice.
"""

from __future__ import annotations

from decimal import Decimal

from cfo.analysis.cost_metrics import PAIRS, detect_anomalies
from cfo.data.people import TEAM_OWNERS
from cfo.db.repo import Repository
from scout.loops.context import JobContext, eur
from scout.models import Job, Task

LOOP = "cost_control"


def _pct(v: Decimal | None) -> str:
    return "new" if v is None else f"{'+' if v >= 0 else ''}{v:.0f}%"


def run(ctx: JobContext, job: Job) -> list[Task]:
    conn = ctx.conn_factory()
    try:
        repo = Repository(conn)
        metrics = repo.list_cost_metrics()
        numbers = {str(m.detail["invoice_number"]) for m in metrics
                   if isinstance(m.detail, dict) and m.detail.get("invoice_number")}
        invoices = {n: inv for n in numbers if (inv := repo.get_invoice_by_number(n)) is not None}
    finally:
        conn.close()
    if not metrics:
        return []
    latest = {}
    for m in metrics:
        latest[m.kind] = max(latest.get(m.kind, m.month), m.month)
    out = []
    for a in detect_anomalies(metrics, invoices):
        if a.month != latest.get(a.kind):
            continue  # only the newest month is news; older ones were (or would have been) alerted back then
        spec = PAIRS[a.kind]
        key = f"metric:{a.kind.value}:{a.month:%Y-%m}"
        if ctx.recorder.by_key(key):
            continue
        task = ctx.recorder.open(key, LOOP, f"{spec.cost_noun} {_pct(a.cost_change_pct)} vs {spec.metric_noun} "
                                            f"{_pct(a.metric_change_pct)} ({a.month:%B})",
                                 amount=-(a.cost - a.previous_cost), category=a.kind.value,
                                 owner_id=TEAM_OWNERS.get(spec.team_id))
        ctx.recorder.step(task.id, "picked_up", "Scout ran the monthly cost check", source="cost_metrics")
        ctx.recorder.step(task.id, "compared", f"Compared {spec.cost_noun.lower()} with {spec.metric_noun}: "
                                               f"{_pct(a.cost_change_pct)} vs {_pct(a.metric_change_pct)}",
                          source="cost_metrics",
                          detail={"cost": str(a.cost), "previous_cost": str(a.previous_cost),
                                  "metric": str(a.metric_value), "previous_metric": str(a.previous_metric_value)})
        if a.drivers:
            d = a.drivers[0]
            ctx.recorder.step(task.id, "explained", f"Biggest driver: {d.label} {eur(d.previous)} → {eur(d.current)}",
                              source="invoicing_pg", detail={"drivers": [
                                  {"label": x.label, "previous": str(x.previous), "current": str(x.current)}
                                  for x in a.drivers]})
        to = [p for p in dict.fromkeys([TEAM_OWNERS.get(spec.team_id), ctx.settings.ceo_id]) if p]
        for who in to:
            ctx.notifier.dm(who, f"Heads-up: {a.explanation}", "NOTIFY", task_id=task.id)
        names = " and ".join(ctx.hr.name(p) for p in to)
        ctx.recorder.step(task.id, "alerted", f"Alerted {names}", source="slack:dm")
        ctx.recorder.set_status(task.id, "done", outcome=a.explanation, resolved_via="auto", level="NOTIFY")
        out.append(ctx.recorder.get(task.id))
    return out
