"""Response models: the contract with the dashboard. The OpenAPI document is generated from these.

Conventions (also in docs/api.md):
- money is an object `{"amount": "1234.50", "currency": "EUR"}`; `amount` is a string decimal with two places
  (negative = outflow / below zero), never a JSON number;
- ratios and percentages are string decimals too (`"50.0"` means +50.0 %);
- dates are ISO 8601 (`2025-12-31`), timestamps are UTC (`2025-12-31T09:30:00Z`), months are first-of-month dates;
- every collection is `{"items": [...]}`; paginated ones also carry `total`, `limit`, `offset`;
- `stub: true` marks data that is not computed from the database yet (also sent as the `X-Stub: true` header).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from cfo.domain import ApprovalStatus, CostMetricKind, DecisionStatus, EventKind, PolicyLevel

DecimalStr = Annotated[str, Field(pattern=r"^-?\d+(\.\d+)?$", examples=["50.0"])]
Stub = Annotated[bool, Field(description="True while the data is a stub, not computed from the database.")]

T = TypeVar("T")


class Money(BaseModel):
    amount: Annotated[str, Field(pattern=r"^-?\d+\.\d{2}$", examples=["1234.50"],
                                 description="String decimal, two places, negative = outflow.")]
    currency: str = Field(default="EUR", examples=["EUR"], description="ISO 4217 code.")


def eur(value: Decimal | str | int | float) -> Money:
    """Build Money in EUR from anything decimal-like, rounded half-up to cents."""
    cents = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Money(amount=format(cents, "f"), currency="EUR")


def pct(value: Decimal | None) -> str | None:
    """Percentage with one decimal as a string, e.g. Decimal('50.0') -> '50.0'."""
    return None if value is None else format(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), "f")


class Page(BaseModel, Generic[T]):
    """Paginated collection (limit/offset)."""

    stub: Stub = False
    items: list[T]
    total: int = Field(description="Number of items matching the filters, across all pages.")
    limit: int
    offset: int


class Collection(BaseModel, Generic[T]):
    """Small collection without pagination."""

    stub: Stub = False
    items: list[T]


# --- /v1/forecast/liquidity ---------------------------------------------------------------------------


class ForecastPoint(BaseModel):
    date: date
    balance: Money = Field(description="Predicted end-of-day balance.")
    lower: Money = Field(description="Lower bound of the 80 % prediction interval.")
    upper: Money = Field(description="Upper bound of the 80 % prediction interval.")


class ForecastGap(BaseModel):
    """A stretch of days with a predicted balance below the safety buffer (D-013); the trigger for a proposal
    (money-market deposit when there is slack, credit line in a what-if scenario)."""

    start: date
    end: date
    lowest_date: date
    lowest_balance: Money


class ForecastModelInfo(BaseModel):
    name: str
    version: str
    mlflow_run_id: str | None = None
    trained_at: datetime | None = None


class LiquidityForecast(BaseModel):
    stub: Stub
    as_of: date = Field(description="Last day with actual data; the forecast starts the day after.")
    horizon_days: int
    granularity: Literal["day", "week"]
    currency: str
    opening_balance: Money
    safety_buffer: Money = Field(description="Safety buffer line to draw (D-013): by default the largest monthly "
                                             "fixed cost (payroll, rent, tax, social security, utilities), "
                                             "overridable with SAFETY_BUFFER_EUR.")
    lowest_point: ForecastPoint = Field(description="Lowest predicted balance within the horizon.")
    gaps: list[ForecastGap] = Field(description="Stretches of days with a predicted balance below `safety_buffer`.")
    model: ForecastModelInfo
    points: list[ForecastPoint]


# --- /v1/kpis -----------------------------------------------------------------------------------------


class UndocumentedPayments(BaseModel):
    count: int = Field(description="Paid invoices without a document in the database (Belegpflicht risk, D-003).")
    total: Money


class ForecastSummary(BaseModel):
    horizon_days: int
    lowest_balance: Money
    lowest_date: date
    first_negative_date: date | None = None
    safety_buffer: Money
    first_below_buffer_date: date | None = Field(default=None, description="First predicted day below the "
                                                                          "safety buffer (D-013), null if none.")


class Kpis(BaseModel):
    stub: Stub
    as_of: date
    balance: Money
    balance_change_30d: Money
    avg_monthly_inflows: Money
    avg_monthly_outflows: Money
    forecast: ForecastSummary
    undocumented_payments: UndocumentedPayments
    pending_approvals: int
    open_cost_alerts: int = Field(description="Cost-vs-metric pairs currently flagged as an anomaly (D-006).")


# --- /v1/costs/by-team --------------------------------------------------------------------------------


class MonthlyCost(BaseModel):
    month: date = Field(description="First day of the month.")
    cost: Money


class TeamCosts(BaseModel):
    team_id: str = Field(examples=["kitchen"])
    team_name: str
    owner_id: str | None = Field(description="Employee who owns these costs.")
    monthly_budget: Money | None
    total: Money
    avg_monthly: Money
    share: DecimalStr = Field(description="Share of all teams' costs, 0..1, four decimals.", examples=["0.6153"])
    by_month: list[MonthlyCost]


class CostsByTeam(BaseModel):
    stub: Stub
    from_: date = Field(alias="from", description="First day included.")
    to: date = Field(description="Last day included.")
    total: Money
    items: list[TeamCosts]

    model_config = {"populate_by_name": True}


# --- /v1/cost-metrics ---------------------------------------------------------------------------------


class AnomalyDriver(BaseModel):
    label: str
    previous: Money
    current: Money


class Anomaly(BaseModel):
    summary: str
    drivers: list[AnomalyDriver]


class CostMetricPoint(BaseModel):
    month: date = Field(description="First day of the month.")
    cost: Money
    metric_value: str = Field(description="Orders / new customers / online orders in the month (integer string).")
    cost_per_unit: Money | None = Field(description="Cost per order / per new customer (CAC) / per online order. "
                                                    "Null when the metric is zero.")
    cost_change_pct: DecimalStr | None = Field(description="Cost change vs previous month in percent.")
    metric_change_pct: DecimalStr | None = Field(description="Metric change vs previous month in percent.")
    anomaly: Anomaly | None = Field(default=None, description="Set when cost grew with no matching metric growth.")


class CostMetricSeries(BaseModel):
    kind: CostMetricKind
    label: str
    team_id: str
    unit: Literal["order", "new_customer", "online_order"] = Field(description="What `cost_per_unit` is per.")
    points: list[CostMetricPoint]


# --- /v1/decisions ------------------------------------------------------------------------------------


class DecisionOut(BaseModel):
    """Mirror of `cfo.domain.Decision`."""

    id: str = Field(examples=["dec_6f1c0a7e2b11"])
    ts: datetime
    action_type: str = Field(examples=["attach_invoice"])
    summary: str
    level: PolicyLevel
    reason: str = Field(description="Why the policy engine chose this level.")
    status: DecisionStatus
    actor: str = Field(description="'agent' or the employee id who decided (e.g. 'vincenzo').")
    payload: dict[str, Any]
    executed_simulated: bool = Field(description="True: executed, but only simulated (D-005); show as SIMULATED.")
    approval_request_id: str | None = None


# --- /v1/rules ----------------------------------------------------------------------------------------


class RuleOut(BaseModel):
    """Mirror of `cfo.domain.Rule`."""

    id: str = Field(examples=["rule_9a41c3d05e77"])
    text: str = Field(description="The CEO's own sentence.")
    level: PolicyLevel
    category: str | None = None
    supplier: str | None = None
    action_type: str | None = None
    created_by: str
    created_at: datetime
    active: bool


# --- /v1/approvals --------------------------------------------------------------------------------------


class ApprovalOut(BaseModel):
    """Mirror of `cfo.domain.ApprovalRequest`, with the decision it is about embedded (for the Approve/Reject card)."""

    id: str = Field(examples=["apr_8e2f6b3a1c47"])
    decision_id: str
    approver_id: str = Field(description="Employee id who may answer (normally the CEO).", examples=["vincenzo"])
    question: str = Field(description="What the approver is asked, in one or two sentences.")
    status: ApprovalStatus
    created_at: datetime
    resolved_at: datetime | None = None
    channel_ref: str | None = Field(default=None, description="Slack message reference, if the DM was sent.")
    decision: DecisionOut | None = Field(
        default=None, description="The pending (or, after the answer, resolved) decision: action_type, summary, payload.")


class ApprovalAnswer(BaseModel):
    """Body of `POST /v1/approvals/{id}:approve` and `:reject`."""

    by: str = Field(
        min_length=1, examples=["vincenzo"],
        description="Employee id of the person answering; must be the approver. Ignored when the server has "
                    "`API_WRITE_TOKEN` set: the token then stands for the CEO.")


class ApprovalResult(ApprovalOut):
    """State of the approval after the call."""

    already_resolved: bool = Field(
        description="True when the approval had been answered before; nothing was executed by this call.")


# --- /v1/events (SSE) and /v1/sim -----------------------------------------------------------------------


class EventOut(BaseModel):
    """Mirror of `cfo.domain.Event` (simulator event: bank transaction, invoice document, metric, ...)."""

    id: str = Field(examples=["evt_3b1c9f0a2d44"])
    ts: datetime
    kind: EventKind
    payload: dict[str, Any]


class SimStatusOut(BaseModel):
    """Mirror of `cfo.sim.SimStatus`: state of the December replay."""

    sim_time: datetime = Field(description="Current simulated time (UTC).")
    paused: bool
    speed: float = Field(description="Simulated seconds per real second.")
    emitted: int = Field(description="Events written so far.")
    total: int = Field(description="Events in the replay.")
    next_event_ts: datetime | None = Field(description="Null when everything was replayed.")
    finished: bool


class SimStart(BaseModel):
    """Optional body of `POST /v1/sim:start`."""

    speed: float | None = Field(default=None, gt=0, description="New speed (sim seconds per real second); keeps the current one when omitted.")


class SimStepResult(BaseModel):
    event: EventOut | None = Field(description="The event that was emitted, null when the replay is finished.")
    status: SimStatusOut


# --- /healthz -----------------------------------------------------------------------------------------


class Health(BaseModel):
    status: Literal["ok"]
    version: str
