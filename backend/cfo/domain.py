"""Typed domain models shared by every module (data, policy, MCP, agent, Slack, API, simulator).

Conventions: money is `Decimal` in EUR (negative = outflow, as in the bank statement), ids are strings,
timestamps are timezone-aware UTC. Every model round-trips through `model_dump(mode="json")` /
`model_validate(...)`, so the same shapes serve the DB, MCP tool results and the REST API.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    """Short unique id such as `dec_3f2a9c1b04d2`."""
    return f"{prefix}_{uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PolicyLevel(StrEnum):
    """Autonomy level of an action (D-005)."""

    AUTO = "AUTO"  # the agent just does it
    NOTIFY = "NOTIFY"  # does it and informs the right person
    APPROVAL = "APPROVAL"  # waits for the CEO to approve


class DecisionStatus(StrEnum):
    EXECUTED = "executed"  # AUTO / NOTIFY actions, and approved ones after execution
    PENDING = "pending"  # APPROVAL: waiting for the CEO
    REJECTED = "rejected"
    ESCALATED = "escalated"  # handed to someone higher (manager chain, D-003)


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CostMetricKind(StrEnum):
    """The three cost-vs-metric pairs (D-006)."""

    FOOD_COST = "food_cost"  # food spend vs orders -> food cost %
    ADS_CAC = "ads_cac"  # ad spend vs new customers -> CAC
    CLOUD_PER_ORDER = (
        "cloud_per_order"  # app cloud spend vs online orders -> cost per order
    )


class EventKind(StrEnum):
    BANK_TRANSACTION = "bank_transaction"  # simulator: new line on the bank statement
    INVOICE_DOCUMENT = (
        "invoice_document"  # simulator: a document appeared on a workstation
    )
    METRIC = "metric"  # simulator: monthly metric / cloud invoice
    REMINDER = "reminder"  # agent timers (e.g. no reply after X hours)
    MESSAGE = "message"  # inbound chat message (Slack)


class Transaction(BaseModel):
    """A line of the company bank statement."""

    id: str = Field(default_factory=lambda: new_id("txn"))
    booking_date: date
    value_date: date | None = None
    type: str  # e.g. "GIRO CARD PAYMENT"
    reference: str  # payment reference text, may contain an invoice number
    counterparty: str
    counterparty_account: str | None = None
    amount: Decimal  # signed, EUR
    currency: str = "EUR"
    invoice_number: str | None = None  # parsed AS-xxxxx / FLV-xxxxx
    category: str | None = None  # e.g. "food", "cloud", "equipment"
    team_id: str | None = None
    documented: bool = False  # a matching Invoice exists (D-003)
    invoice_id: str | None = None


class InvoiceLine(BaseModel):
    description: str
    quantity: Decimal = Decimal(1)
    unit_price: Decimal
    amount: Decimal
    category: str | None = None  # FOCUS-like service/cost category for cloud invoices


class Invoice(BaseModel):
    """Supplier invoice (expenses only, D-003)."""

    id: str = Field(default_factory=lambda: new_id("inv"))
    number: str  # AS-69664, FLV-1234, ...
    supplier: str
    issue_date: date
    due_date: date | None = None
    total: Decimal
    currency: str = "EUR"
    lines: list[InvoiceLine] = Field(default_factory=list)
    transaction_id: str | None = None  # the payment it documents
    source: str = "db"  # "db" or a workstation path the agent found it at
    category: str | None = (
        None  # "food", "beverages", "cloud", "advertising", "equipment"
    )
    team_id: str | None = None  # the team whose costs it belongs to


class Vacation(BaseModel):
    start: date
    end: date  # inclusive

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


class Employee(BaseModel):
    id: str  # slug, e.g. "grazyna"
    name: str
    role: str
    team_id: str | None = None
    manager_id: str | None = None  # None for the CEO; escalation walks this chain
    vacations: list[Vacation] = Field(default_factory=list)
    slack_user_id: str | None = None

    def on_vacation(self, day: date) -> bool:
        return any(v.covers(day) for v in self.vacations)


class Team(BaseModel):
    id: str  # "kitchen", "front", "marketing", "it", "equipment"
    name: str
    owner_id: str | None = None  # the employee who owns the costs
    monthly_budget: Decimal | None = None


class CostMetric(BaseModel):
    """One month of one cost-vs-metric pair (D-006)."""

    id: str = Field(default_factory=lambda: new_id("cm"))
    kind: CostMetricKind
    month: date  # first day of the month
    team_id: str | None = None
    cost: Decimal  # EUR spent
    metric_value: Decimal  # orders / new customers / online orders
    detail: dict[str, Any] = Field(default_factory=dict)  # e.g. per-line cloud costs

    @property
    def cost_per_unit(self) -> Decimal | None:
        """Food cost % basis, CAC or cost per order; None when the metric is zero."""
        return self.cost / self.metric_value if self.metric_value else None


class Event(BaseModel):
    """Something that happened in the world; the simulator emits them, the agent reacts."""

    id: str = Field(default_factory=lambda: new_id("evt"))
    ts: datetime = Field(default_factory=utcnow)
    kind: EventKind
    payload: dict[str, Any] = Field(default_factory=dict)


class Decision(BaseModel):
    """History entry of everything the agent decided or did, shown on /v1/decisions."""

    id: str = Field(default_factory=lambda: new_id("dec"))
    ts: datetime = Field(default_factory=utcnow)
    action_type: (
        str  # e.g. "attach_invoice", "request_credit_line", "alert_cost_anomaly"
    )
    summary: str  # one human-readable sentence
    level: PolicyLevel
    reason: str  # why that level (from the policy engine)
    status: DecisionStatus = DecisionStatus.EXECUTED
    actor: str = "agent"  # "agent" or an employee id (CEO approvals)
    payload: dict[str, Any] = Field(default_factory=dict)
    executed_simulated: bool = (
        False  # execution is never real (D-005): shown as SIMULATED
    )
    approval_request_id: str | None = None


class ApprovalRequest(BaseModel):
    """A pending question to a person with Approve / Reject (LangGraph interrupt, resumed on answer)."""

    id: str = Field(default_factory=lambda: new_id("apr"))
    decision_id: str
    approver_id: str  # employee id, normally the CEO
    question: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = Field(default_factory=utcnow)
    resolved_at: datetime | None = None
    channel_ref: str | None = (
        None  # e.g. Slack channel + message ts, for updating the message
    )


class Rule(BaseModel):
    """CEO override of the default policy, e.g. "equipment: always ask me". Newest matching rule wins."""

    id: str = Field(default_factory=lambda: new_id("rule"))
    text: str  # the CEO's own sentence
    level: PolicyLevel
    category: str | None = None  # match transactions/actions of this category ...
    supplier: str | None = None  # ... and/or this supplier ...
    action_type: str | None = None  # ... and/or this action type
    created_by: str = "ceo"
    created_at: datetime = Field(default_factory=utcnow)
    active: bool = True
