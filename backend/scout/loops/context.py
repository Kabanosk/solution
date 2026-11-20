"""What every loop gets: recorder, audit, connectors, LLM, a notifier, the job queue, settings and HR."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from cfo.llm import LLM
from scout.audit import Audit
from scout.config import ScoutSettings
from scout.connectors.registry import Connectors
from scout.db import ConnFactory
from scout.jobs import JobQueue
from scout.tasks import TaskRecorder

INVOICE_NUMBER_RE = re.compile(r"\b([A-Z]{2,5}-\d{2,6}(?:-\d{2})?)\b")

ROLE_CODES = (("ceo", "ceo"), ("owner", "ceo"), ("office manager", "office_manager"), ("chef", "head_chef"),
              ("baker", "baker"), ("waitstaff", "waitstaff"), ("marketing", "waitstaff"))


@dataclass(frozen=True)
class Button:
    action_id: str  # scout_approve | scout_reject
    label: str
    value: str


class Notifier(Protocol):
    def dm(self, employee_id: str, text: str, level: str, *, task_id: str | None = None,
           buttons: Sequence[Button] = (), thread: str | None = None) -> str | None:
        """Send a DM (or a reply in `thread`, "channel:ts"); returns "channel:ts" of the message."""

    def update(self, ref: str, text: str, level: str) -> None:
        """Replace the text of a message sent before (and drop its buttons)."""

    def track(self, task_id: str, status_ref: str) -> None:
        """Show this task's steps live in the "Working on it…" message `status_ref`."""


class LogNotifier:
    """No Slack configured: messages go to the log and to `sent` (tests)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.tracked: dict[str, str] = {}

    def dm(self, employee_id, text, level, *, task_id=None, buttons=(), thread=None):  # noqa: ANN001
        self.sent.append({"to": employee_id, "text": text, "level": level, "task_id": task_id,
                          "buttons": list(buttons), "thread": thread})
        return f"D_{employee_id}:{len(self.sent)}.0"

    def update(self, ref, text, level):  # noqa: ANN001
        self.updates.append({"ref": ref, "text": text, "level": level})

    def track(self, task_id, status_ref):  # noqa: ANN001
        self.tracked[task_id] = status_ref


class HR:
    """People, roles, vacations and the manager chain (company HR tables)."""

    def __init__(self, conn_factory: ConnFactory, user_map: dict[str, str] | None = None):
        self._cf = conn_factory
        self._by_slack = {v: k for k, v in (user_map or {}).items()}

    def employee(self, employee_id: str) -> dict[str, Any] | None:
        with self._cf() as conn:
            return conn.execute("SELECT * FROM employees WHERE id = %s", (employee_id,)).fetchone()

    def by_slack_id(self, slack_id: str) -> dict[str, Any] | None:
        if slack_id in self._by_slack:
            return self.employee(self._by_slack[slack_id])
        with self._cf() as conn:
            return conn.execute("SELECT * FROM employees WHERE slack_user_id = %s", (slack_id,)).fetchone()

    def all(self) -> list[dict[str, Any]]:
        with self._cf() as conn:
            return conn.execute("SELECT * FROM employees ORDER BY id").fetchall()

    def name(self, employee_id: str | None) -> str:
        if not employee_id:
            return "someone"
        e = self.employee(employee_id)
        return (e or {}).get("name", "").split()[0] or employee_id

    def role(self, employee_id: str) -> str:
        text = ((self.employee(employee_id) or {}).get("role") or "").casefold()
        return next((code for needle, code in ROLE_CODES if needle in text), "staff")

    def on_vacation(self, employee_id: str, day: date) -> bool:
        with self._cf() as conn:
            row = conn.execute("SELECT 1 FROM vacations WHERE employee_id = %s AND start_date <= %s AND end_date >= %s",
                               (employee_id, day, day)).fetchone()
        return row is not None

    def manager(self, employee_id: str) -> str | None:
        return (self.employee(employee_id) or {}).get("manager_id")

    def chain(self, employee_id: str) -> list[str]:
        out, cur = [], employee_id
        while cur and cur not in out:
            out.append(cur)
            cur = self.manager(cur)
        return out

    def first_available(self, employee_id: str, day: date) -> tuple[str, list[str]]:
        """The first person in the chain who is not on vacation, and who was skipped."""
        chain = self.chain(employee_id)
        for i, who in enumerate(chain):
            if not self.on_vacation(who, day):
                return who, chain[:i]
        return chain[-1], chain[:-1]


@dataclass
class JobContext:
    conn_factory: ConnFactory
    recorder: TaskRecorder
    audit: Audit
    connectors: Connectors
    llm: LLM
    notifier: Notifier
    jobs: JobQueue
    settings: ScoutSettings
    hr: HR
    clock: Any = field(default=lambda: datetime.now(timezone.utc))
    checkpointer: Any = None  # LangGraph saver for approvals (Postgres in the worker, in-memory in tests)
    forecaster: Any = None  # (ctx, outflows) -> LiquidityReview; default: cfo.forecast.liquidity on the company data

    def now(self) -> datetime:
        return self.clock()


def eur(v: Decimal | str | float) -> str:
    return f"€{abs(Decimal(str(v))):,.2f}"


def invoice_number_in(text: str) -> str | None:
    m = INVOICE_NUMBER_RE.search(text or "")
    return m.group(1) if m else None
