"""Stub data in the final response shapes, with realistic Pizzeria Amalfi numbers (D-002, T002 data).

Every function returns finished response models with `stub=True`. Tasks that wire a resource to the database
(T019 and others) replace the call in the router, not the shape. The picture is "after the December replay",
i.e. as of 2025-12-31, so every step of the golden path (demo-flow.md) is visible in the data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from cfo.domain import CostMetricKind, DecisionStatus, PolicyLevel
from cfo.api.schemas import (
    Anomaly,
    AnomalyDriver,
    CostMetricPoint,
    CostMetricSeries,
    CostsByTeam,
    DecisionOut,
    ForecastGap,
    ForecastModelInfo,
    ForecastPoint,
    ForecastSummary,
    Kpis,
    LiquidityForecast,
    MonthlyCost,
    RuleOut,
    TeamCosts,
    UndocumentedPayments,
    eur,
    pct,
)

AS_OF = date(2025, 12, 31)
BALANCE = Decimal("42338.93")  # 32,945.51 at the end of November + December net 9,393.42
PENDING_APPROVALS = 1

# --- liquidity forecast -------------------------------------------------------------------------------

# Anchor balances (end of day); the stub interpolates linearly between them. Shape follows 2025:
# slow decline in spring, deep trough in June (about -20k EUR, demo step 5), recovery in autumn.
_ANCHORS: list[tuple[date, Decimal]] = [
    (date(2025, 12, 31), BALANCE),
    (date(2026, 1, 31), Decimal("39100.00")),
    (date(2026, 2, 28), Decimal("35200.00")),
    (date(2026, 3, 31), Decimal("30400.00")),
    (date(2026, 4, 30), Decimal("21800.00")),
    (date(2026, 5, 31), Decimal("4900.00")),
    (date(2026, 6, 27), Decimal("-20430.00")),
    (date(2026, 7, 31), Decimal("3500.00")),
    (date(2026, 8, 31), Decimal("14000.00")),
    (date(2026, 9, 30), Decimal("22000.00")),
    (date(2026, 12, 31), Decimal("40000.00")),
]
MAX_HORIZON_DAYS = (_ANCHORS[-1][0] - AS_OF).days  # 365


def _balance_on(day: date) -> Decimal:
    for (d0, b0), (d1, b1) in zip(_ANCHORS, _ANCHORS[1:]):
        if d0 <= day <= d1:
            span = (d1 - d0).days
            return b0 + (b1 - b0) * Decimal((day - d0).days) / Decimal(span)
    raise ValueError(f"{day} outside the stub range")


def _point(day: date) -> ForecastPoint:
    balance = _balance_on(day)
    # The 80 % interval widens with the distance from the last actual day: 400 EUR plus 22 EUR per day ahead.
    band = Decimal("400") + Decimal((day - AS_OF).days) * Decimal("22")
    return ForecastPoint(date=day, balance=eur(balance), lower=eur(balance - band), upper=eur(balance + band))


def liquidity_forecast(horizon_days: int = 90, granularity: str = "week") -> LiquidityForecast:
    days = [AS_OF + timedelta(days=i) for i in range(1, horizon_days + 1)]
    daily = {d: _balance_on(d) for d in days}
    lowest_day = min(days, key=lambda d: daily[d])
    gaps: list[ForecastGap] = []
    run: list[date] = []
    for d in days + [None]:
        if d is not None and daily[d] < 0:
            run.append(d)
        elif run:
            low = min(run, key=lambda x: daily[x])
            gaps.append(ForecastGap(start=run[0], end=run[-1], lowest_date=low, lowest_balance=eur(daily[low])))
            run = []
    if granularity == "week":
        sampled = days[6::7]
        if lowest_day not in sampled:  # never hide the trough from a chart
            sampled = sorted(sampled + [lowest_day])
    else:
        sampled = days
    return LiquidityForecast(
        stub=True,
        as_of=AS_OF,
        horizon_days=horizon_days,
        granularity=granularity,  # type: ignore[arg-type]
        currency="EUR",
        opening_balance=eur(BALANCE),
        lowest_point=_point(lowest_day),
        gaps=gaps,
        model=ForecastModelInfo(name="xgboost-liquidity", version="stub", mlflow_run_id=None, trained_at=None),
        points=[_point(d) for d in sampled],
    )


def forecast_summary(horizon_days: int = MAX_HORIZON_DAYS) -> ForecastSummary:
    f = liquidity_forecast(horizon_days, "week")
    return ForecastSummary(
        horizon_days=horizon_days,
        lowest_balance=f.lowest_point.balance,
        lowest_date=f.lowest_point.date,
        first_negative_date=f.gaps[0].start if f.gaps else None,
    )


# --- cost / metric pairs ------------------------------------------------------------------------------

_MONTHS = [date(2025, m, 1) for m in range(1, 13)]

# (cost EUR, metric) per month Jan..Dec 2025, from data/generated/metrics/cost_metrics.json.
_FOOD = [("24290.95", 1989), ("22134.08", 1922), ("24092.38", 1997), ("21763.52", 1796), ("24741.18", 2121),
         ("0", 0), ("22841.45", 2297), ("24306.94", 2607), ("23124.17", 2418), ("23264.66", 2549),
         ("22544.94", 2145), ("23914.45", 2454)]
_ADS = [("778.15", 53), ("881.10", 79), ("875.30", 71), ("888.30", 74), ("720.67", 54), ("210.68", 5),
        ("774.70", 68), ("822.20", 74), ("863.02", 59), ("837.54", 62), ("837.73", 56), ("783.05", 60)]
_CLOUD = [("210.42", 118), ("212.70", 128), ("218.06", 155), ("216.70", 145), ("224.78", 187), ("191.34", 0),
          ("234.19", 234), ("242.84", 279), ("244.91", 288), ("249.56", 311), ("244.01", 278), ("366.02", 286)]

_ANOMALY_COST_PCT = Decimal(20)  # cost up by 20 % or more ...
_ANOMALY_METRIC_PCT = Decimal(10)  # ... while the metric grew by less than 10 %

_CLOUD_DEC_ANOMALY = Anomaly(
    summary="Cloud cost for the ordering app rose 50.0 % month over month while online orders grew only 2.9 %: "
            "log ingestion and retention went from EUR 26.95 to EUR 147.23, cost per online order from "
            "EUR 0.88 to EUR 1.28.",
    drivers=[
        AnomalyDriver(label="Log ingestion and retention", previous=eur("26.95"), current=eur("147.23")),
        AnomalyDriver(label="Compute (app backend)", previous=eur("127.43"), current=eur("128.44")),
    ],
)


def _change(new: Decimal, old: Decimal) -> Decimal | None:
    return None if old == 0 else (new - old) / old * 100


def _series(kind: CostMetricKind, label: str, team_id: str, unit: str, rows: list[tuple[str, int]]) -> CostMetricSeries:
    points: list[CostMetricPoint] = []
    prev: tuple[Decimal, Decimal] | None = None
    for month, (cost_s, metric) in zip(_MONTHS, rows):
        cost, value = Decimal(cost_s), Decimal(metric)
        cost_chg = _change(cost, prev[0]) if prev else None
        metric_chg = _change(value, prev[1]) if prev else None
        flagged = (cost_chg is not None and metric_chg is not None
                   and cost_chg >= _ANOMALY_COST_PCT and metric_chg < _ANOMALY_METRIC_PCT)
        points.append(CostMetricPoint(
            month=month,
            cost=eur(cost),
            metric_value=str(metric),
            cost_per_unit=eur(cost / value) if value else None,
            cost_change_pct=pct(cost_chg),
            metric_change_pct=pct(metric_chg),
            anomaly=_CLOUD_DEC_ANOMALY if flagged and kind == CostMetricKind.CLOUD_PER_ORDER else None,
        ))
        prev = (cost, value)
    return CostMetricSeries(kind=kind, label=label, team_id=team_id, unit=unit, points=points)  # type: ignore[arg-type]


def cost_metric_series() -> list[CostMetricSeries]:
    return [
        _series(CostMetricKind.FOOD_COST, "Food cost vs orders", "kitchen", "order", _FOOD),
        _series(CostMetricKind.ADS_CAC, "Advertising vs new customers (CAC)", "marketing", "new_customer", _ADS),
        _series(CostMetricKind.CLOUD_PER_ORDER, "App cloud vs online orders", "it", "online_order", _CLOUD),
    ]


def open_cost_alerts() -> int:
    return sum(1 for s in cost_metric_series() if s.points[-1].anomaly is not None)


# --- costs by team ------------------------------------------------------------------------------------

# Monthly spend per team Jan..Dec 2025. Kitchen and front come from the bank statement (supplier payments
# and payroll; June is the shutdown month), marketing and IT from the ad and cloud invoices.
_TEAMS: dict[str, tuple[str, str, str | None, list[str]]] = {
    "kitchen": ("Kitchen / food", "rosa", "28300.00", [
        "28741.13", "26584.40", "28542.25", "26214.31", "29191.02", "4450.00",
        "27291.11", "28757.19", "27574.08", "27715.46", "26995.73", "27610.52"]),
    "front": ("Front of house and admin", "vincenzo", "9500.00", ["8600.00"] * 12),
    "marketing": ("Marketing", "vanessa", "900.00", [c for c, _ in _ADS]),
    "it": ("IT / online-order app", "vincenzo", "300.00", [c for c, _ in _CLOUD]),
    "equipment": ("Equipment", "yusuf", "500.00", ["0.00"] * 9 + ["3590.20", "0.00", "0.00"]),
}


def costs_by_team(start: date | None = None, end: date | None = None) -> CostsByTeam:
    start = start or date(2025, 1, 1)
    end = end or AS_OF
    months = [m for m in _MONTHS if start <= m <= end or (m.year, m.month) == (start.year, start.month)]
    months = [m for m in months if m <= end]
    idx = [_MONTHS.index(m) for m in months]
    sums: dict[str, Decimal] = {t: sum((Decimal(costs[i]) for i in idx), Decimal(0)) for t, (*_, costs) in _TEAMS.items()}
    grand = sum(sums.values(), Decimal(0))
    items = []
    for team_id, (name, owner, budget, costs) in _TEAMS.items():
        total = sums[team_id]
        items.append(TeamCosts(
            team_id=team_id,
            team_name=name,
            owner_id=owner,
            monthly_budget=eur(budget),
            total=eur(total),
            avg_monthly=eur(total / len(months)) if months else eur(0),
            share=format((total / grand).quantize(Decimal("0.0001")), "f") if grand else "0.0000",
            by_month=[MonthlyCost(month=_MONTHS[i], cost=eur(costs[i])) for i in idx],
        ))
    items.sort(key=lambda t: Decimal(t.total.amount), reverse=True)
    return CostsByTeam(stub=True, **{"from": start}, to=end, total=eur(grand), items=items)


# --- decisions ----------------------------------------------------------------------------------------


def _ts(day: int, hh: int, mm: int, ss: int = 0, month: int = 12) -> datetime:
    return datetime(2025, month, day, hh, mm, ss, tzinfo=timezone.utc)


_DECISIONS: list[DecisionOut] = [
    DecisionOut(
        id="dec_5b7e41c0a9d3", ts=_ts(31, 14, 0), action_type="pay_invoice",
        summary="Pay invoice EQ-2026-0001 (EUR 1,280.00) from Gastro Tech Ltd for a dough mixer.",
        level=PolicyLevel.APPROVAL, status=DecisionStatus.PENDING, actor="agent",
        reason="CEO rule: equipment always needs my approval.",
        payload={"supplier": "Gastro Tech Ltd", "amount": "-1280.00", "currency": "EUR", "category": "equipment",
                 "rule_id": "rule_3c9d7a1e5f20"},
        executed_simulated=False, approval_request_id="apr_8e2f6b3a1c47"),
    DecisionOut(
        id="dec_0a4c9e27b6f1", ts=_ts(31, 10, 20), action_type="create_rule",
        summary="New rule from Vincenzo: equipment purchases always need his approval.",
        level=PolicyLevel.AUTO, status=DecisionStatus.EXECUTED, actor="vincenzo",
        reason="CEO instruction in Slack, stored as a policy rule.",
        payload={"rule_id": "rule_3c9d7a1e5f20", "text": "I always approve equipment myself", "category": "equipment"},
        executed_simulated=False),
    DecisionOut(
        id="dec_c71d3f80e5a2", ts=_ts(31, 9, 42, 15), action_type="request_credit_line",
        summary="Credit line of EUR 25,000 approved by Vincenzo; application sent to the bank (simulated).",
        level=PolicyLevel.APPROVAL, status=DecisionStatus.EXECUTED, actor="vincenzo",
        reason="Credit lines always need CEO approval.",
        payload={"amount": "25000.00", "currency": "EUR", "lowest_forecast_balance": "-20430.00",
                 "lowest_forecast_date": "2026-06-27", "forecast_model": "xgboost-liquidity"},
        executed_simulated=True, approval_request_id="apr_f4a90d12b7c8"),
    DecisionOut(
        id="dec_92e6b0d4a1c5", ts=_ts(31, 8, 5), action_type="alert_cost_anomaly",
        summary="Cloud invoice CLD-2025-12 is up 50.0 % (EUR 244.01 to EUR 366.02), online orders only up 2.9 %.",
        level=PolicyLevel.NOTIFY, status=DecisionStatus.EXECUTED, actor="agent",
        reason="Unusual amount for a known supplier, below 5 % of monthly inflows: do it and inform.",
        payload={"invoice_number": "CLD-2025-12", "kind": "cloud_per_order", "driver": "Log ingestion and retention",
                 "notified": ["vincenzo"]},
        executed_simulated=False),
    DecisionOut(
        id="dec_3e8a5c17d902", ts=_ts(30, 16, 5), action_type="escalate_missing_invoice",
        summary="No document for FLV-42026 (EUR 869.62, Fattoria La Vino GmbH). Grazyna is on vacation, "
                "so Vincenzo was asked directly.",
        level=PolicyLevel.NOTIFY, status=DecisionStatus.ESCALATED, actor="agent",
        reason="Missing invoice is a tax risk (Belegpflicht); owner unavailable, escalated up the manager chain.",
        payload={"transaction_id": "txn_1116", "invoice_number": "FLV-42026", "amount": "-869.62",
                 "escalated_from": "grazyna", "escalated_to": "vincenzo"},
        executed_simulated=False),
    DecisionOut(
        id="dec_6f1c0a7e2b11", ts=_ts(30, 9, 14, 22), action_type="attach_invoice",
        summary="Found invoice AS-69664 in Grazyna's Downloads, attached it to the payment of EUR 623.48.",
        level=PolicyLevel.AUTO, status=DecisionStatus.EXECUTED, actor="agent",
        reason="Routine: known supplier, amount within 20 % of the typical payment.",
        payload={"transaction_id": "txn_1120", "invoice_number": "AS-69664", "amount": "-623.48",
                 "source": "grazyna/Downloads/AS-69664.pdf", "employee_notified": "grazyna"},
        executed_simulated=False),
]


def decisions(level: str | None = None, status: str | None = None, action_type: str | None = None) -> list[DecisionOut]:
    """Newest first, filtered."""
    return [d for d in _DECISIONS
            if (level is None or d.level.value == level)
            and (status is None or d.status.value == status)
            and (action_type is None or d.action_type == action_type)]


def decision(decision_id: str) -> DecisionOut | None:
    return next((d for d in _DECISIONS if d.id == decision_id), None)


# --- rules --------------------------------------------------------------------------------------------

_RULES: list[RuleOut] = [
    RuleOut(id="rule_3c9d7a1e5f20", text="I always approve equipment myself", level=PolicyLevel.APPROVAL,
            category="equipment", created_by="vincenzo", created_at=_ts(31, 10, 20), active=True),
    RuleOut(id="rule_b20e84d6c913", text="Payments to Asimex are fine, no need to ask me", level=PolicyLevel.AUTO,
            supplier="Asimex Wholesale Ltd", created_by="vincenzo", created_at=_ts(14, 15, 3, month=11),
            active=True),
    RuleOut(id="rule_71a5f0c8e342", text="Tell me about every advertising invoice", level=PolicyLevel.NOTIFY,
            category="advertising", created_by="vincenzo", created_at=_ts(3, 9, 30, month=11), active=False),
]


def rules(active: bool | None = None) -> list[RuleOut]:
    """Newest first."""
    return [r for r in _RULES if active is None or r.active == active]


# --- kpis ---------------------------------------------------------------------------------------------


def kpis() -> Kpis:
    return Kpis(
        stub=True,
        as_of=AS_OF,
        balance=eur(BALANCE),
        balance_change_30d=eur("9393.42"),
        avg_monthly_inflows=eur("44474.52"),
        avg_monthly_outflows=eur("-42333.42"),
        forecast=forecast_summary(),
        undocumented_payments=UndocumentedPayments(count=8, total=eur("6571.98")),
        pending_approvals=PENDING_APPROVALS,
        open_cost_alerts=open_cost_alerts(),
    )
