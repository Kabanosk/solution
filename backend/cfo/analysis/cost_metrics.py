"""Cost vs metric analysis for the three D-006 pairs.

    food cost  <-> orders            (food cost per order)
    ad spend   <-> new customers     (CAC)
    app cloud  <-> online orders     (cloud cost per online order)

`monthly_series` turns `CostMetric` rows into per-pair monthly series with per-unit cost and month-over-month
changes. `detect_anomalies` flags a month where the cost grew by at least `cost_growth_pct` while the metric grew
by less than `metric_growth_max_pct` (cost growth without metric growth), names the invoice or bill lines that
contribute most to the increase and words one sentence about it. All maths is code (Decimal); the sentence is
a template, the agent may let the LLM rephrase it (`cfo.agent.investigate`).

The series shape matches `GET /v1/cost-metrics` (`cfo.api.schemas.CostMetricSeries`); `to_api_series` converts.

Where the line items come from, in this order, per month (`CostMetric.detail`):
1. `{"lines": [{"service"|"description"|"name": ..., "cost"|"amount": ...}]}` (cloud bills),
2. `{"by_supplier": {name: amount}}` or a flat `{name: amount}` mapping (food payments),
3. the invoice named by `detail["invoice_number"]` (ads): its lines.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from cfo.api.schemas import Anomaly, AnomalyDriver, CostMetricPoint, CostMetricSeries, eur, pct
from cfo.domain import CostMetric, CostMetricKind, Invoice

COST_GROWTH_PCT = Decimal(20)  # cost up by at least this much vs the previous month ...
METRIC_GROWTH_MAX_PCT = Decimal(10)  # ... while the metric grew by less than this
MAX_DRIVERS = 3

Unit = Literal["order", "new_customer", "online_order"]


@dataclass(frozen=True)
class PairSpec:
    label: str
    team_id: str
    unit: Unit
    cost_noun: str  # "Cloud cost for the ordering app"
    metric_noun: str  # "online orders"
    unit_cost_noun: str  # "cloud cost per online order"


PAIRS: dict[CostMetricKind, PairSpec] = {
    CostMetricKind.FOOD_COST: PairSpec(
        "Food cost vs orders", "kitchen", "order", "Food cost", "orders", "food cost per order"),
    CostMetricKind.ADS_CAC: PairSpec(
        "Advertising vs new customers (CAC)", "marketing", "new_customer", "Ad spend", "new customers",
        "cost per new customer (CAC)"),
    CostMetricKind.CLOUD_PER_ORDER: PairSpec(
        "App cloud vs online orders", "it", "online_order", "Cloud cost for the ordering app", "online orders",
        "cloud cost per online order"),
}


@dataclass(frozen=True)
class Driver:
    """A line item whose amount rose from one month to the next."""

    label: str
    previous: Decimal
    current: Decimal

    @property
    def delta(self) -> Decimal:
        return self.current - self.previous


@dataclass(frozen=True)
class CostAnomaly:
    kind: CostMetricKind
    month: date
    team_id: str | None
    cost: Decimal
    previous_cost: Decimal
    cost_change_pct: Decimal  # Decimal("Infinity") when the previous cost was zero (any cost is then new)
    metric_value: Decimal
    previous_metric_value: Decimal
    metric_change_pct: Decimal  # 0 when the metric stayed at zero
    cost_per_unit: Decimal | None
    previous_cost_per_unit: Decimal | None
    cost_per_unit_change_pct: Decimal | None
    drivers: list[Driver]  # largest increase first, only lines that rose
    explanation: str


@dataclass(frozen=True)
class Point:
    month: date
    cost: Decimal
    metric_value: Decimal
    cost_per_unit: Decimal | None  # None when the metric is zero
    cost_change_pct: Decimal | None  # None for the first month or when the previous cost is zero
    metric_change_pct: Decimal | None
    cost_per_unit_change_pct: Decimal | None
    anomaly: CostAnomaly | None = None


@dataclass(frozen=True)
class Series:
    kind: CostMetricKind
    label: str
    team_id: str
    unit: Unit
    points: list[Point] = field(default_factory=list)


# -- helpers -----------------------------------------------------------------------------------------------
def change_pct(new: Decimal | None, old: Decimal | None) -> Decimal | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / old * 100


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _invoice_index(invoices: Iterable[Invoice] | Mapping[str, Invoice] | None) -> dict[str, Invoice]:
    if invoices is None:
        return {}
    if isinstance(invoices, Mapping):
        return dict(invoices)
    return {i.number: i for i in invoices}


def line_items(metric: CostMetric, invoices: Mapping[str, Invoice] | None = None) -> dict[str, Decimal]:
    """Named amounts that make up the month's cost (see the module docstring for the sources)."""
    detail = metric.detail if isinstance(metric.detail, dict) else {}
    out: dict[str, Decimal] = {}
    lines = detail.get("lines")
    if isinstance(lines, list):
        for it in lines:
            if not isinstance(it, dict):
                continue
            name = next((it[k] for k in ("service", "description", "name") if it.get(k)), None)
            amount = _decimal(next((it[k] for k in ("cost", "amount") if k in it), None))
            if name and amount is not None:
                out[str(name)] = out.get(str(name), Decimal(0)) + amount
        if out:
            return out
    flat = detail.get("by_supplier") if isinstance(detail.get("by_supplier"), dict) else detail
    for k, v in flat.items():
        if k != "invoice_number" and (amount := _decimal(v)) is not None:
            out[str(k)] = amount
    if out:
        return out
    inv = (invoices or {}).get(str(detail.get("invoice_number")))
    if inv is not None:
        for ln in inv.lines:
            out[ln.description] = out.get(ln.description, Decimal(0)) + ln.amount
    return out


def _drivers(prev: CostMetric, cur: CostMetric, invoices: Mapping[str, Invoice]) -> list[Driver]:
    before, now = line_items(prev, invoices), line_items(cur, invoices)
    found = [Driver(k, before.get(k, Decimal(0)), v) for k, v in now.items()]
    found = [d for d in found if d.delta > 0]
    found.sort(key=lambda d: (-d.delta, d.label))
    return found[:MAX_DRIVERS]


def _money(value: Decimal) -> str:
    return "EUR " + format(value.quantize(Decimal("0.01")), "f")


def _p1(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.1")), "f")


def _explain(kind: CostMetricKind, cost_pct: Decimal, metric_pct: Decimal, drivers: list[Driver],
             cpu_prev: Decimal | None, cpu_now: Decimal | None, cost_prev: Decimal, cost_now: Decimal) -> str:
    spec = PAIRS[kind]
    if metric_pct > 0:
        metric_phrase = f"grew only {_p1(metric_pct)}%"
    elif metric_pct < 0:
        metric_phrase = f"fell {_p1(abs(metric_pct))}%"
    else:
        metric_phrase = "did not change"
    parts = []
    if drivers:
        d = drivers[0]
        parts.append(f"{d.label} went from {_money(d.previous)} to {_money(d.current)}")
    if cpu_prev is not None and cpu_now is not None:
        parts.append(f"{spec.unit_cost_noun} from {_money(cpu_prev)} to {_money(cpu_now)}")
    tail = f": {', '.join(parts)}." if parts else "."
    rose = (f"rose {_p1(cost_pct)}%" if cost_pct.is_finite()
            else f"went from {_money(cost_prev)} to {_money(cost_now)}")
    return f"{spec.cost_noun} {rose} month over month while {spec.metric_noun} {metric_phrase}{tail}"


def _assess(prev: CostMetric, cur: CostMetric, invoices: Mapping[str, Invoice], cost_growth_pct: Decimal,
            metric_growth_max_pct: Decimal) -> CostAnomaly | None:
    if not _consecutive(prev, cur):  # a gap is not month over month
        return None
    cost_pct = change_pct(cur.cost, prev.cost)
    if cost_pct is None:
        # previous cost was zero: any cost is new growth (never a silent skip on division by zero)
        if cur.cost <= 0:
            return None
        cost_pct = Decimal("Infinity")
    elif cost_pct < cost_growth_pct:
        return None
    if prev.metric_value == 0:
        if cur.metric_value != 0:  # restarting from zero (e.g. after the June shutdown): growth is unbounded
            return None
        metric_pct = Decimal(0)  # stayed at zero
    else:
        metric_pct = change_pct(cur.metric_value, prev.metric_value)
        assert metric_pct is not None
    if metric_pct >= metric_growth_max_pct:
        return None
    cpu_now, cpu_prev = cur.cost_per_unit, prev.cost_per_unit
    drivers = _drivers(prev, cur, invoices)
    return CostAnomaly(
        kind=cur.kind, month=cur.month, team_id=cur.team_id, cost=cur.cost, previous_cost=prev.cost,
        cost_change_pct=cost_pct, metric_value=cur.metric_value, previous_metric_value=prev.metric_value,
        metric_change_pct=metric_pct, cost_per_unit=cpu_now, previous_cost_per_unit=cpu_prev,
        cost_per_unit_change_pct=change_pct(cpu_now, cpu_prev), drivers=drivers,
        explanation=_explain(cur.kind, cost_pct, metric_pct, drivers, cpu_prev, cpu_now, prev.cost, cur.cost),
    )


def _consecutive(prev: CostMetric, cur: CostMetric) -> bool:
    """True when `cur` is the calendar month right after `prev`."""
    return (cur.month.year * 12 + cur.month.month) - (prev.month.year * 12 + prev.month.month) == 1


def _by_kind(metrics: Iterable[CostMetric]) -> dict[CostMetricKind, list[CostMetric]]:
    grouped: dict[CostMetricKind, list[CostMetric]] = {}
    for m in sorted(metrics, key=lambda m: m.month):
        grouped.setdefault(CostMetricKind(m.kind), []).append(m)
    return grouped


# -- public API --------------------------------------------------------------------------------------------
def monthly_series(metrics: Iterable[CostMetric], invoices: Iterable[Invoice] | Mapping[str, Invoice] | None = None,
                   *, cost_growth_pct: Decimal | float = COST_GROWTH_PCT,
                   metric_growth_max_pct: Decimal | float = METRIC_GROWTH_MAX_PCT) -> list[Series]:
    """One series per pair present in `metrics` (food, ads, cloud order), months ascending, anomalies attached."""
    inv, grow, cap = _invoice_index(invoices), Decimal(str(cost_growth_pct)), Decimal(str(metric_growth_max_pct))
    grouped = _by_kind(metrics)
    out: list[Series] = []
    for kind, spec in PAIRS.items():
        rows = grouped.get(kind)
        if not rows:
            continue
        points: list[Point] = []
        for i, cur in enumerate(rows):
            prev = rows[i - 1] if i and _consecutive(rows[i - 1], cur) else None
            points.append(Point(
                month=cur.month, cost=cur.cost, metric_value=cur.metric_value, cost_per_unit=cur.cost_per_unit,
                cost_change_pct=change_pct(cur.cost, prev.cost) if prev else None,
                metric_change_pct=change_pct(cur.metric_value, prev.metric_value) if prev else None,
                cost_per_unit_change_pct=change_pct(cur.cost_per_unit, prev.cost_per_unit) if prev else None,
                anomaly=_assess(prev, cur, inv, grow, cap) if prev else None,
            ))
        out.append(Series(kind=kind, label=spec.label, team_id=rows[-1].team_id or spec.team_id, unit=spec.unit,
                          points=points))
    return out


def detect_anomalies(metrics: Iterable[CostMetric], invoices: Iterable[Invoice] | Mapping[str, Invoice] | None = None,
                     *, cost_growth_pct: Decimal | float = COST_GROWTH_PCT,
                     metric_growth_max_pct: Decimal | float = METRIC_GROWTH_MAX_PCT) -> list[CostAnomaly]:
    """Months where a cost grew without its metric, oldest first (then pair order)."""
    found = [p.anomaly for s in monthly_series(metrics, invoices, cost_growth_pct=cost_growth_pct,
                                               metric_growth_max_pct=metric_growth_max_pct)
             for p in s.points if p.anomaly is not None]
    order = list(PAIRS)
    return sorted(found, key=lambda a: (a.month, order.index(a.kind)))


def to_api_series(series: Iterable[Series]) -> list[CostMetricSeries]:
    """Convert to the `/v1/cost-metrics` response items (`cfo.api.schemas.CostMetricSeries`)."""
    items = []
    for s in series:
        points = []
        for p in s.points:
            anomaly = None
            if p.anomaly is not None:
                anomaly = Anomaly(summary=p.anomaly.explanation, drivers=[
                    AnomalyDriver(label=d.label, previous=eur(d.previous), current=eur(d.current))
                    for d in p.anomaly.drivers])
            points.append(CostMetricPoint(
                month=p.month, cost=eur(p.cost), metric_value=str(int(p.metric_value)),
                cost_per_unit=eur(p.cost_per_unit) if p.cost_per_unit is not None else None,
                cost_change_pct=pct(p.cost_change_pct), metric_change_pct=pct(p.metric_change_pct), anomaly=anomaly))
        items.append(CostMetricSeries(kind=s.kind, label=s.label, team_id=s.team_id, unit=s.unit, points=points))
    return items


def load_series(repo: Any, **thresholds: Any) -> list[Series]:
    """Series from a `cfo.db.repo.Repository` (cost metrics plus the invoices their details point at)."""
    metrics = repo.list_cost_metrics()
    numbers = {str(m.detail["invoice_number"]) for m in metrics if isinstance(m.detail, dict) and m.detail.get("invoice_number")}
    invoices = {n: inv for n in numbers if (inv := repo.get_invoice_by_number(n)) is not None}
    return monthly_series(metrics, invoices, **thresholds)
