"""One durable CEO strategy note per local day. Recommendations never execute spending or send messages."""
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from scout.loops.context import JobContext
from scout.models import Job, Task
from scout.strategy import fetch_news, growth_scenarios


def run(ctx: JobContext, job: Job) -> Task:
    now = ctx.now()
    today = now.astimezone(ZoneInfo("Europe/Berlin")).date()
    day = date.fromisoformat(job.payload.get("day", today.isoformat()))
    key = f"strategy:{day}"
    existing = ctx.recorder.by_key(key)
    if existing and existing.status == "done":
        return existing
    task = ctx.recorder.open(key, "strategy", f"CEO daily briefing · {day}", owner_id=ctx.settings.ceo_id or None,
                             category="strategy", level="AUTO")
    with ctx.conn_factory() as conn:
        rows = conn.execute("SELECT booking_date, amount, currency, category FROM transactions "
                            "WHERE booking_date >= %s AND booking_date <= %s ORDER BY booking_date", (today - timedelta(days=400), today)).fetchall()
    growth = growth_scenarios(rows, today, ctx.settings.strategy_currency)
    categories = {r["category"] for r in rows if r["category"]}
    news = fetch_news(now, categories)
    target_value = ctx.settings.strategy_monthly_revenue_target
    target = {"status": "unknown", "target_monthly_revenue": str(target_value) if target_value else None,
              "basis": "Latest completed month of bank-collected revenue", "currency": ctx.settings.strategy_currency}
    scorecard = {
        "revenue": target,
        "operating_surplus": {"status": "unknown", "target": str(ctx.settings.strategy_operating_surplus_target) if ctx.settings.strategy_operating_surplus_target is not None else None,
            "missing_inputs": ["Reconciled operating receipts/payments separated from financing, tax and investment flows"],
            "note": "Operating cash surplus is not accounting profit."},
        "owner_income": {"status": "unknown", "target": str(ctx.settings.strategy_owner_income_target) if ctx.settings.strategy_owner_income_target is not None else None,
            "missing_inputs": ["Owner salary/drawings identified separately from business spending"]},
        "expansion": {"status": "unknown", "missing_inputs": ["Hiring or new-location cost", "Start date", "Minimum cash reserve", "Capacity and expected incremental sales"],
            "next_step": "Run a liquidity what-if before committing to a hire or new location."},
    }
    recommendations = []
    if target_value is None:
        recommendations.append("Set a monthly collected-revenue target with the CEO, plus a profit target and minimum cash reserve.")
    elif growth["status"] == "scenario":
        latest = Decimal(growth["monthly_revenue"][-1]["revenue"])
        target["status"] = "on_track" if latest >= target_value else "below_target"
        target["gap"] = str((latest-target_value).quantize(Decimal('.01')))
        if latest < target_value:
            recommendations.append("Revenue is below the stated target: review order volume and average ticket before choosing a growth experiment.")
    if growth["status"] != "scenario":
        recommendations.append("Refresh and reconcile revenue history; growth and target progress cannot yet be assessed reliably.")
    else:
        recommendations.append("Test one small demand experiment; measure incremental orders, average ticket and contribution margin over the next two weeks.")
    recommendations.append("Before expansion or hiring, run the existing liquidity what-if with the proposed cost and retain the CEO's cash reserve.")
    if news["status"] != "ok":
        recommendations.append("News coverage is incomplete; retry unavailable sources before concluding there are no market risks.")
    lines = [f"CEO daily briefing · {day}", f"Sales target: {target['status'].replace('_', ' ')}."]
    if target_value is not None:
        lines.append(f"Monthly collected-revenue target: {target_value} {growth['currency']}." +
                     (f" Latest completed-month gap: {target['gap']} {growth['currency']}." if "gap" in target else " Progress unknown until data is sufficient and current."))
    if growth["monthly_revenue"]:
        latest = growth["monthly_revenue"][-1]
        lines.append(f"Latest completed revenue month: {latest['month'][:7]} · {latest['revenue']} {growth['currency']} (bank-collected sales proxy).")
    if growth["scenarios"]:
        lines.append(f"Observed median monthly growth: {growth['observed_growth_pct']}%. Three-month scenarios (not predictions): " + "; ".join(
            f"{s['name']} {s['months'][-1]['revenue']} {growth['currency']}" for s in growth["scenarios"]) + ".")
    else:
        lines.append(f"Growth outlook: {growth['status'].replace('_', ' ')}.")
    lines.append(f"Market watch: {news['status']}; {len(news['items'])} potentially relevant items from the last 14 days.")
    for item in news["items"][:3]:
        lines.append(f"• {item['title']} ({item['source']}, {item['published_at'][:10]}). {item['relevance']} {item['action']} {item['url']}")
    if not news["items"] and news["status"] == "ok":
        lines.append("No matching items in the checked feeds; this is not comprehensive market coverage.")
    lines.append("Scorecard: operating cash surplus unknown (not accounting profit); owner income unknown; expansion readiness unknown. Add reconciled operating flows, tagged owner drawings, expansion costs and a cash reserve.")
    lines.append("Next steps: " + " ".join(recommendations))
    lines.append("Limits: " + " ".join(growth["limitations"]))
    report = {"kind": "strategy_briefing", "as_of": today.isoformat(), "scheduled_day": day.isoformat(), "ledger_as_of": max((r["booking_date"].isoformat() for r in rows), default=None),
              "growth": growth, "news": news, "recommendations": recommendations, "target": target, "scorecard": scorecard}
    ctx.recorder.step(task.id, "reviewed", "Reviewed completed revenue months and public news", source="strategy",
                      detail=report)
    # Replace evidence on retry: a crash before closing cannot duplicate the daily briefing.
    ctx.recorder.update(task.id, evidence=[report, *[{"kind": "news", "label": i["title"], "url": i["url"], "published_at": i["published_at"], "source": i["source"]} for i in news["items"]]])
    return ctx.recorder.set_status(task.id, "done", outcome="\n".join(lines), resolved_via="auto", level="AUTO")
