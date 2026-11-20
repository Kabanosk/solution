"""Liquidity gaps, what-if scenarios and the proposals that follow (T016, D-013, D-005).

* A **gap** is a stretch where the predicted balance is below the **safety buffer** (not below zero). The default
  buffer is one month of fixed costs (`default_buffer`: the most expensive calendar month of payroll, rent, tax,
  social security and utilities in the history, about 17.7k EUR for Pizzeria Amalfi). Configurable per call.
* `forecast_with(extra_outflows=[...])` is the CEO's "what if I buy an oven for 30k in March": the same forecast as
  `liquidity_forecast`, with the extra payments on top.
* `propose_credit_line(gap)` turns a gap into an APPROVAL proposal: amount (shortfall plus a reserve, rounded up),
  period, and a plain-language reason built from the model's SHAP drivers (`ForecastModel.explain`).
* `find_surplus` + `propose_investment(surplus)`: when the balance stays well above the buffer but a weak month
  (June: shutdown) is ahead, park a sum in a money market fund / deposit sized so that the rest of the horizon,
  June included, still stays above the buffer.
* Both proposals are classified by `cfo.agent.policy.classify` (credit line and investment: always APPROVAL unless
  a CEO rule says otherwise) and convert to the METRIC event `cfo.agent.graph.Agent.handle` consumes
  (`LiquidityProposal.to_event()`), which raises the approval interrupt. Execution is simulated by the agent.

Amounts in the public models are `Decimal` EUR; the pandas forecast underneath is float.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

import pandas as pd
import psycopg
from pydantic import BaseModel, Field

from cfo.agent.policy import PolicyAction, PolicyContext, classify
from cfo.domain import Event, EventKind, PolicyLevel
from cfo.forecast import features as feat
from cfo.forecast import model as fm

FIXED_COST_CATEGORIES = tuple(feat.DEFAULT_DUE_DAYS)  # payroll, rent, tax, social_security, utilities
DEFAULT_HORIZON_DAYS = 240  # from the end of the history to about the end of July of the following year
CREDIT_RESERVE = Decimal("0.20")  # a credit line is the shortfall plus 20%, rounded up
CREDIT_ROUNDING = Decimal("1000")
CREDIT_LEAD_DAYS = 14  # the line should be in place before the balance first crosses the buffer
CREDIT_TAIL_DAYS = 30  # and stay for a month after it recovers
INVESTMENT_STEP = Decimal("500")
MIN_INVESTMENT = Decimal("1000")  # below this the surplus is not worth a proposal

CREDIT_LINE = "request_credit_line"
INVESTMENT = "make_investment"
LIQUIDITY_SIGNAL = "liquidity_forecast"  # METRIC signal handled by the agent graph

_CENT = Decimal("0.01")

# How a model feature reads to the owner (T015 feature names).
FEATURE_PHRASES = {
    "shutdown_flag": "the summer shutdown",
    "month": "the usual seasonal pattern of the month",
    "quarter": "the usual seasonal pattern of the quarter",
    "day_of_year": "the usual seasonal pattern of the year",
    "week_of_year": "the usual seasonal pattern of the week",
    "weekday": "the weekday pattern",
    "is_weekend": "weekends",
    "day_of_month": "the day-of-month pattern",
    "days_to_month_end": "the position in the month",
    "is_month_start": "the start of the month",
    "is_month_end": "the end of the month",
    "is_public_holiday": "public holidays",
    "payroll_due": "payroll being due",
    "rent_due": "rent being due",
    "tax_due": "tax payments being due",
    "social_security_due": "social security being due",
    "utilities_due": "utilities being due",
}


# -- models ----------------------------------------------------------------------------------------------
class Outflow(BaseModel):
    """An extra payment in a what-if scenario. `amount` is a positive EUR figure (money leaving the account)."""

    on: date
    amount: Decimal
    label: str = "extra outflow"


class LiquidityGap(BaseModel):
    """A stretch of days with the predicted balance below the safety buffer."""

    start: date
    end: date  # last day below the buffer
    lowest_date: date
    lowest_balance: Decimal
    buffer: Decimal
    shortfall: Decimal  # buffer - lowest_balance
    recovery_date: date | None = None  # first day back at or above the buffer; None: still below at horizon end

    @property
    def month(self) -> str:
        return self.lowest_date.strftime("%Y-%m")


class Surplus(BaseModel):
    """Cash that can be parked without touching the buffer anywhere in the horizon."""

    invest_on: date  # the balance peak before the weak month
    amount: Decimal  # largest sum (rounded down) that keeps every later day at or above the buffer
    weak_month: str  # YYYY-MM with the lowest predicted net flow
    weak_month_net: Decimal
    balance_on_invest_date: Decimal
    lowest_balance: Decimal  # lowest balance from invest_on on, before investing
    lowest_date: date
    buffer: Decimal


class LiquidityProposal(BaseModel):
    """What the agent asks the CEO for. `reason` is plain language with numbers from code."""

    action_type: str  # request_credit_line | make_investment (both strategic: APPROVAL)
    instrument: str  # "credit line" | "money market fund"
    amount: Decimal
    period_start: date
    period_end: date
    month: str  # the month the proposal is about (YYYY-MM)
    min_balance: Decimal  # lowest predicted balance (before the proposal)
    buffer: Decimal
    reason: str
    drivers: list[dict[str, Any]] = Field(default_factory=list)  # SHAP drivers of the month (T015 `explain`)
    importances: dict[str, float] = Field(default_factory=dict)
    level: PolicyLevel
    policy_reason: str

    @property
    def needs_approval(self) -> bool:
        return self.level == PolicyLevel.APPROVAL

    def to_payload(self) -> dict[str, Any]:
        """The METRIC payload of `Agent.handle`: strings for money, JSON-safe."""
        return {
            "signal": LIQUIDITY_SIGNAL, "action_type": self.action_type, "month": self.month,
            "min_balance": str(self.min_balance), "requested_amount": str(self.amount), "buffer": str(self.buffer),
            "instrument": self.instrument, "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(), "why": self.reason,
        }

    def to_event(self) -> Event:
        return Event(kind=EventKind.METRIC, payload=self.to_payload())


@dataclass
class Evidence:
    """What explanations are built from. Without a model/transactions the reason stays generic."""

    model: fm.ForecastModel | None = None
    transactions: pd.DataFrame | None = None
    outflows: Sequence[Outflow] = field(default_factory=tuple)


class LiquidityReview(BaseModel, arbitrary_types_allowed=True):
    forecast: pd.DataFrame
    buffer: Decimal
    gaps: list[LiquidityGap]
    proposals: list[LiquidityProposal]


# -- helpers ---------------------------------------------------------------------------------------------
def _dec(value: float | Decimal | str) -> Decimal:
    return Decimal(str(value)).quantize(_CENT)


def _eur(value: Decimal | float) -> str:
    return f"{Decimal(str(value)):,.0f} EUR"


def _round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(ROUND_CEILING) * step


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(ROUND_FLOOR) * step


def _month_label(ts: pd.Timestamp | date) -> str:
    return pd.Timestamp(ts).strftime("%B %Y")


def fixed_costs_monthly(transactions: pd.DataFrame) -> Decimal:
    """The most expensive calendar month of fixed costs (payroll, rent, tax, social security, utilities)."""
    fixed = transactions[transactions["category"].isin(FIXED_COST_CATEGORIES)]
    if fixed.empty:
        raise ValueError("no fixed-cost bookings: cannot derive a safety buffer, pass buffer= explicitly")
    per_month = (-fixed["amount"]).groupby(fixed["booking_date"].dt.to_period("M")).sum()
    return _dec(per_month.max())


def default_buffer(transactions: pd.DataFrame) -> Decimal:
    """One month of fixed costs (D-013)."""
    return fixed_costs_monthly(transactions)


def _normalize_outflows(extra: Iterable[Any]) -> list[Outflow]:
    out: list[Outflow] = []
    for item in extra or ():
        if isinstance(item, Outflow):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(Outflow(on=item.get("on", item.get("date")), amount=item["amount"],
                               label=item.get("label", "extra outflow")))
        else:
            on, amount, *label = item
            out.append(Outflow(on=pd.Timestamp(on).date(), amount=Decimal(str(amount)),
                               label=label[0] if label else "extra outflow"))
    for o in out:
        if o.amount <= 0:
            raise ValueError("extra outflow amounts are positive EUR (money leaving the account)")
    return out


# -- forecast and what-if --------------------------------------------------------------------------------
def forecast_with(
    extra_outflows: Iterable[Any] = (),
    *,
    days: int = DEFAULT_HORIZON_DAYS,
    start_balance: float | None = None,
    model: fm.ForecastModel | None = None,
    transactions: pd.DataFrame | None = None,
    conn: psycopg.Connection | None = None,
    scheduled_payments: Any = None,
) -> pd.DataFrame:
    """`liquidity_forecast` with extra payments, e.g. `forecast_with([Outflow(on=date(2026, 3, 15), amount=30000,
    label="oven")])`. Also accepts `(date, amount[, label])` tuples and dicts. With no extras it is the base
    forecast. Extra outflows are positive EUR and must fall inside the horizon.

    Same frame as `liquidity_forecast` plus the column `what_if` (the extra payments, negative); `attrs` adds
    `default_buffer` (float, one month of fixed costs) and `outflows`.
    """
    tx = transactions if transactions is not None else feat.fetch_transactions(conn)
    outflows = _normalize_outflows(extra_outflows)
    base_schedule = fm.partner_tranche_schedule(tx) if scheduled_payments is None else fm._as_series(scheduled_payments)
    sums: dict[pd.Timestamp, float] = {}
    for o in outflows:  # several outflows on one day add up
        sums[pd.Timestamp(o.on)] = sums.get(pd.Timestamp(o.on), 0.0) - float(o.amount)
    extra = fm._as_series(sums or None)
    combined = base_schedule.add(extra, fill_value=0.0)
    fc = fm.liquidity_forecast(days, start_balance, model=model, transactions=tx,
                               scheduled_payments=combined)
    first, last = fc.index[0].date(), fc.index[-1].date()
    for o in outflows:
        if not first <= o.on <= last:
            raise ValueError(f"outflow '{o.label}' on {o.on} is outside the forecast horizon {first}..{last}")
    fc["what_if"] = extra.reindex(fc.index).fillna(0.0)
    fc.attrs.update(default_buffer=float(default_buffer(tx)), outflows=outflows)
    return fc


# -- gaps ------------------------------------------------------------------------------------------------
def detect_gaps(
    forecast: pd.DataFrame,
    buffer: float | Decimal | None = None,
    *,
    transactions: pd.DataFrame | None = None,
) -> list[LiquidityGap]:
    """Stretches where the predicted balance is below `buffer` (default: one month of fixed costs, from the
    forecast itself when made by `forecast_with`, else from `transactions`). Days at exactly the buffer are fine."""
    limit = _resolve_buffer(forecast, buffer, transactions)
    below = forecast["balance"] < float(limit)
    groups = (below != below.shift(fill_value=False)).cumsum()
    gaps: list[LiquidityGap] = []
    for _, chunk in forecast[below].groupby(groups[below]):
        lowest_day = chunk["balance"].idxmin()
        lowest = _dec(chunk.loc[lowest_day, "balance"])
        later = forecast.loc[forecast.index > chunk.index[-1]]
        recovery = later.index[0].date() if not later.empty else None
        gaps.append(LiquidityGap(
            start=chunk.index[0].date(), end=chunk.index[-1].date(), lowest_date=lowest_day.date(),
            lowest_balance=lowest, buffer=limit, shortfall=_dec(limit - lowest), recovery_date=recovery))
    return gaps


def _resolve_buffer(forecast: pd.DataFrame, buffer: Any, transactions: pd.DataFrame | None) -> Decimal:
    if buffer is not None:
        return _dec(buffer)
    if "default_buffer" in forecast.attrs:
        return _dec(forecast.attrs["default_buffer"])
    if transactions is not None:
        return default_buffer(transactions)
    raise ValueError("no buffer: pass buffer=, transactions=, or a forecast made by forecast_with")


# -- explanations ----------------------------------------------------------------------------------------
def _driver_phrase(driver: Mapping[str, Any]) -> str:
    name = FEATURE_PHRASES.get(driver["feature"], driver["feature"].replace("_", " "))
    value = Decimal(str(driver["contribution_eur"]))
    return f"{name} ({'-' if value < 0 else '+'}{abs(value):,.0f} EUR)"


def _typical_monthly_revenue(tx: pd.DataFrame) -> Decimal | None:
    rev = tx[tx["category"] == "revenue"]
    if rev.empty:
        return None
    per_month = rev["amount"].groupby(rev["booking_date"].dt.to_period("M")).sum()
    return _dec(per_month.median())


def _month_facts(ev: Evidence, month: pd.Timestamp) -> tuple[list[str], list[dict], dict[str, float]]:
    """Sentences about one month plus the SHAP drivers and importances behind them."""
    month = month.to_period("M").to_timestamp()
    end = month + pd.offsets.MonthEnd(0)
    sentences: list[str] = []
    drivers: list[dict] = []
    importances: dict[str, float] = {}
    fixed = None
    if ev.transactions is not None:
        try:
            fixed = fixed_costs_monthly(ev.transactions)
        except ValueError:
            fixed = None
    closed = 0
    if ev.model is not None:
        closed = sum(1 for d in range(1, month.days_in_month + 1) if (month.month, d) in ev.model.shutdown_days)
    if closed >= 7:
        weeks = max(1, round(closed / 7))
        text = f"the restaurant is closed about {weeks} week{'s' if weeks != 1 else ''}"
        typical = _typical_monthly_revenue(ev.transactions) if ev.transactions is not None else None
        if typical is not None:
            drop = typical * Decimal(closed) / Decimal(month.days_in_month)
            text += f", so revenue drops by about {_eur(_round_down(drop, Decimal('100')))} against a normal month"
        if fixed is not None:
            text += f", while fixed costs of {_eur(fixed)} per month continue"
        sentences.append(f"{_month_label(month)}: {text}.")
    if ev.model is not None:
        why = ev.model.explain(month, end, top=3)
        drivers, importances = why["top_drivers"], why["feature_importances"]
        net = Decimal(str(why["predicted_net_eur"]))
        if drivers and net < 0:
            sentences.append(
                f"The model expects a net cash flow of {'-' if net < 0 else '+'}{_eur(abs(net))} in {_month_label(month)}, "
                f"mainly because of {'; '.join(_driver_phrase(x) for x in drivers)}.")
    return sentences, drivers, importances


def _driver_month(ev: Evidence, gap: LiquidityGap) -> pd.Timestamp:
    """The month whose weak cash flow produced the gap: the month with the lowest predicted net flow between
    45 days before the gap and its lowest point (the trough can fall just after the month, e.g. 2 July for June).
    Falls back to the month the gap starts in."""
    start_month = pd.Timestamp(gap.start).to_period("M").to_timestamp()
    if ev.model is None:
        return start_month
    days = pd.date_range(pd.Timestamp(gap.start) - pd.Timedelta(days=45), pd.Timestamp(gap.lowest_date), freq="D")
    nets = ev.model.predict(days).resample("MS").sum()
    weakest = nets.idxmin()
    return weakest if nets.min() < 0 else start_month


def _outflow_sentence(outflows: Sequence[Outflow], until: date) -> str | None:
    planned = [o for o in outflows if o.on <= until]
    if not planned:
        return None
    return "Planned in this scenario: " + ", ".join(
        f"{o.label} {_eur(o.amount)} on {o.on.isoformat()}" for o in planned) + "."


# -- proposals -------------------------------------------------------------------------------------------
def propose_credit_line(
    gap: LiquidityGap | Sequence[LiquidityGap],
    evidence: Evidence | None = None,
    *,
    policy_context: PolicyContext | None = None,
    reserve: Decimal = CREDIT_RESERVE,
) -> LiquidityProposal:
    """Credit line proposal for one gap, or for several (sized for the deepest, running across all of them).

    Amount = shortfall below the buffer of the deepest gap plus `reserve` (20%), rounded up to 1,000 EUR. Period:
    from 14 days before the first gap to 30 days after the last one recovers. APPROVAL via `classify`.
    """
    gaps = [gap] if isinstance(gap, LiquidityGap) else sorted(gap, key=lambda g: g.start)
    if not gaps:
        raise ValueError("no gap: nothing to propose a credit line for")
    ev = evidence or Evidence()
    deepest = max(gaps, key=lambda g: g.shortfall)
    amount = _round_up(deepest.shortfall * (1 + reserve), CREDIT_ROUNDING)
    start = pd.Timestamp(gaps[0].start) - pd.Timedelta(days=CREDIT_LEAD_DAYS)
    last = gaps[-1]
    end = pd.Timestamp(last.recovery_date or last.end) + pd.Timedelta(days=CREDIT_TAIL_DAYS)
    driver_month = _driver_month(ev, deepest)
    sentences, drivers, importances = _month_facts(ev, driver_month)
    planned = _outflow_sentence(ev.outflows, deepest.lowest_date)
    parts = [*sentences[:1], *([planned] if planned else []), *sentences[1:]]
    parts.append(
        f"The balance is predicted to fall to {_eur(deepest.lowest_balance)} on {deepest.lowest_date.isoformat()}, "
        f"{_eur(deepest.shortfall)} below the safety buffer of {_eur(deepest.buffer)} (one month of fixed costs).")
    parts.append(
        f"A credit line of {_eur(amount)} from {start.date().isoformat()} to {end.date().isoformat()} "
        "covers the shortfall with a reserve.")
    pol = classify(PolicyAction(action_type=CREDIT_LINE, amount=amount), policy_context or PolicyContext())
    return LiquidityProposal(
        action_type=CREDIT_LINE, instrument="credit line", amount=amount, period_start=start.date(),
        period_end=end.date(), month=pd.Timestamp(deepest.start).strftime("%Y-%m"),
        min_balance=deepest.lowest_balance, buffer=deepest.buffer,
        reason=" ".join(parts), drivers=drivers, importances=importances, level=pol.level, policy_reason=pol.reason)


def find_surplus(
    forecast: pd.DataFrame,
    buffer: float | Decimal | None = None,
    *,
    transactions: pd.DataFrame | None = None,
    min_amount: Decimal = MIN_INVESTMENT,
    step: Decimal = INVESTMENT_STEP,
) -> Surplus | None:
    """Money that can be parked: None when there is a gap, no weak month ahead, or too little headroom.

    The weak month is the complete forecast month with the lowest predicted net flow (it must be negative and
    not the first month). Investing happens at the balance peak before it; the sum is the lowest balance from
    that day to the end of the horizon minus the buffer, rounded down to `step`, so no later day (June
    included) drops below the buffer.
    """
    limit = _resolve_buffer(forecast, buffer, transactions)
    if (forecast["balance"] < float(limit)).any():
        return None
    net = forecast["predicted_net"].resample("MS").sum()
    full = forecast["predicted_net"].resample("MS").count() == net.index.days_in_month
    weak = net[full & (net.index > forecast.index[0])]
    if weak.empty or weak.min() >= 0:
        return None
    weak_start = weak.idxmin()
    before = forecast.loc[forecast.index < weak_start, "balance"]
    if before.empty:
        return None
    peak_day = before.idxmax()
    after = forecast.loc[forecast.index >= peak_day, "balance"]
    low_day = after.idxmin()
    amount = _round_down(_dec(after.min()) - limit, step)
    if amount < min_amount:
        return None
    return Surplus(
        invest_on=peak_day.date(), amount=amount, weak_month=weak_start.strftime("%Y-%m"),
        weak_month_net=_dec(weak.min()), balance_on_invest_date=_dec(before.max()),
        lowest_balance=_dec(after.min()), lowest_date=low_day.date(), buffer=limit)


def propose_investment(
    surplus: Surplus,
    evidence: Evidence | None = None,
    *,
    policy_context: PolicyContext | None = None,
    instrument: str = "money market fund",
) -> LiquidityProposal:
    """Investment proposal (money market fund by default: redeemable daily) for a `Surplus`. APPROVAL."""
    ev = evidence or Evidence()
    weak = pd.Timestamp(surplus.weak_month + "-01")
    sentences, drivers, importances = _month_facts(ev, weak)
    after = surplus.lowest_balance - surplus.amount
    parts = sentences[:1] or [f"{_month_label(weak)} is the weakest month ahead "
                              f"(net {_eur(surplus.weak_month_net)})."]
    parts.extend(sentences[1:])
    parts.append(
        f"From {surplus.invest_on.isoformat()} the balance stays above the safety buffer of {_eur(surplus.buffer)} "
        f"(lowest {_eur(surplus.lowest_balance)} on {surplus.lowest_date.isoformat()}), so {_eur(surplus.amount)} "
        f"can earn interest in a {instrument} and the balance still never falls below {_eur(after)}, "
        f"{_eur(after - surplus.buffer)} above the buffer.")
    month_end = (weak + pd.offsets.MonthEnd(0)).date()
    pol = classify(PolicyAction(action_type=INVESTMENT, amount=surplus.amount), policy_context or PolicyContext())
    return LiquidityProposal(
        action_type=INVESTMENT, instrument=instrument, amount=surplus.amount, period_start=surplus.invest_on,
        period_end=month_end, month=surplus.weak_month, min_balance=after, buffer=surplus.buffer,
        reason=" ".join(parts), drivers=drivers, importances=importances, level=pol.level, policy_reason=pol.reason)


# -- the whole review ------------------------------------------------------------------------------------
def review_liquidity(
    extra_outflows: Iterable[Any] = (),
    *,
    buffer: float | Decimal | None = None,
    days: int = DEFAULT_HORIZON_DAYS,
    start_balance: float | None = None,
    model: fm.ForecastModel | None = None,
    transactions: pd.DataFrame | None = None,
    conn: psycopg.Connection | None = None,
    policy_context: PolicyContext | None = None,
) -> LiquidityReview:
    """Forecast (with optional what-if outflows), detect gaps, and propose: a credit line when there are gaps,
    otherwise an investment when there is surplus ahead of a weak month, otherwise nothing."""
    tx = transactions if transactions is not None else feat.fetch_transactions(conn)
    model = model or fm.get_model(tx)
    outflows = _normalize_outflows(extra_outflows)
    fc = forecast_with(outflows, days=days, start_balance=start_balance, model=model, transactions=tx)
    limit = _resolve_buffer(fc, buffer, tx)
    evidence = Evidence(model=model, transactions=tx, outflows=outflows)
    gaps = detect_gaps(fc, limit)
    proposals: list[LiquidityProposal] = []
    if gaps:
        proposals.append(propose_credit_line(gaps, evidence, policy_context=policy_context))
    else:
        surplus = find_surplus(fc, limit)
        if surplus is not None:
            proposals.append(propose_investment(surplus, evidence, policy_context=policy_context))
    return LiquidityReview(forecast=fc, buffer=limit, gaps=gaps, proposals=proposals)


__all__ = [
    "CREDIT_LINE", "INVESTMENT", "LIQUIDITY_SIGNAL", "Evidence", "LiquidityGap", "LiquidityProposal",
    "LiquidityReview", "Outflow", "Surplus", "default_buffer", "detect_gaps", "find_surplus", "fixed_costs_monthly",
    "forecast_with", "propose_credit_line", "propose_investment", "review_liquidity",
]
