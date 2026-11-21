"""Typed records shared by the connectors, loops, Slack UI and API. Money is Decimal EUR, signed."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["todo", "in_progress", "waiting", "escalated", "done", "cancelled"]
Loop = Literal["invoicing", "cost_control", "liquidity", "chat", "expenses", "strategy", "forecast"]
ResolvedVia = Literal["invoicing", "files", "email", "chat_context", "slack_dm", "approval", "auto", "forecast"]
Level = Literal["AUTO", "NOTIFY", "APPROVAL"]


class Task(BaseModel):
    id: str
    dedupe_key: str
    loop: Loop
    title: str
    status: TaskStatus
    owner_id: str | None = None
    requested_by: str = "agent"
    resolved_via: ResolvedVia | None = None
    outcome: str | None = None
    level: Level | None = None
    simulated: bool = False
    category: str | None = None
    amount: Decimal | None = None
    currency: str = "EUR"
    counterparty: str | None = None
    transaction_id: str | None = None
    slack_ref: str | None = None
    approval_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None

    @property
    def open(self) -> bool:
        return self.status not in ("done", "cancelled")


class Step(BaseModel):
    id: int
    task_id: str
    ts: datetime
    kind: str
    source: str | None = None
    result_count: int | None = None
    text: str
    detail: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] | None = None


class Job(BaseModel):
    id: int
    kind: str
    payload: dict[str, Any]
    dedupe_key: str
    status: str
    attempts: int
    run_after: datetime
    task_id: str | None = None
    error: str | None = None


class Approval(BaseModel):
    id: str
    task_id: str
    approver_id: str
    question: str
    status: Literal["pending", "approved", "rejected"]
    answered_by: str | None = None
    answered_at: datetime | None = None
    slack_ref: str | None = None
    created_at: datetime


class AuditEntry(BaseModel):
    id: int
    ts: datetime
    task_id: str | None = None
    actor: str
    requester: str | None = None
    role: str | None = None
    action: str
    target: str
    resource: str
    decision: Literal["ALLOW", "DENY", "MASK", "BLOCK"]
    policy: str | None = None
    reason: str | None = None


class BankTxn(BaseModel):
    id: str
    booked_at: datetime
    amount: Decimal
    currency: str = "EUR"
    counterparty: str
    reference: str
    type: str = "transfer"  # card_payment | transfer | card_settlement
    card_last4: str | None = None
    holder_employee_id: str | None = None

    @property
    def outgoing(self) -> bool:
        return self.amount < 0


class MailHit(BaseModel):
    id: str
    mailbox: str
    sender: str
    subject: str
    date: datetime | None = None
    snippet: str = ""
    body: str = ""
    attachments: list[str] = Field(default_factory=list)


class ChatHit(BaseModel):
    channel: str
    ts: str
    user: str | None = None
    user_name: str | None = None
    text: str
    permalink: str | None = None


class InvoiceDoc(BaseModel):
    number: str
    supplier: str
    issue_date: date
    total: Decimal
    currency: str | None = None  # ISO code of `total`; None = the source did not say (read from text): the payment's
    lines: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "db"  # where it was found: db | files:<path> | email:<id> | slack:<file>
    evidence: dict[str, Any] | None = None
    invoice_id: str | None = None
    transaction_id: str | None = None
