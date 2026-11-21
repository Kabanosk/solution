"""What wakes Scout up: new bank transactions, new mail for a waiting task, and the clock.

Each watcher keeps its cursor in `watcher_state` and only enqueues jobs (idempotent dedupe keys), so running two
watchers by mistake, or polling twice, never creates two tasks.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from cfo.data.source import _category
from scout.connectors.registry import Connectors
from scout.db import ConnFactory
from scout.jobs import JobQueue
from scout.loops.context import invoice_number_in
from scout.tasks import TaskRecorder

log = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo("Europe/Berlin")


def _cursor(cf: ConnFactory, name: str) -> dict[str, Any]:
    with cf() as conn:
        row = conn.execute("SELECT cursor FROM watcher_state WHERE name = %s", (name,)).fetchone()
    return dict(row["cursor"]) if row else {}


def _save(cf: ConnFactory, name: str, cursor: dict[str, Any]) -> None:
    with cf() as conn:
        conn.execute("INSERT INTO watcher_state (name, cursor) VALUES (%s, %s) ON CONFLICT (name) DO UPDATE SET "
                     "cursor = EXCLUDED.cursor, updated_at = now()", (name, Jsonb(cursor)))
        conn.commit()


class BankWatcher:
    """New transactions from the bank API -> the company ledger, and a `bank_txn` job per outgoing payment."""

    def __init__(self, conn_factory: ConnFactory, connectors: Connectors, jobs: JobQueue):
        self._cf, self.connectors, self._jobs = conn_factory, connectors, jobs

    def poll(self) -> int:
        cur = _cursor(self._cf, "bank")
        since = datetime.fromisoformat(cur["since"]) if cur.get("since") else datetime.now(timezone.utc)
        txns = sorted(self.connectors.bank.list_transactions(since), key=lambda t: t.booked_at)
        n = 0
        for t in txns:
            if t.booked_at <= since and t.id in set(cur.get("ids_at_since", [])):
                continue
            category, team = _category(t.type.upper().replace("_", " "), t.counterparty, t.reference)
            with self._cf() as conn:
                conn.execute(
                    "INSERT INTO transactions (id, booking_date, value_date, type, reference, counterparty, amount, "
                    "currency, invoice_number, category, team_id, documented) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE) ON CONFLICT (id) DO NOTHING",
                    (t.id, t.booked_at.date(), t.booked_at.date(), t.type, t.reference, t.counterparty, t.amount,
                     t.currency, invoice_number_in(t.reference), category, team))
                if t.amount > 0:  # money in: does it settle an expected refund?
                    conn.execute(
                        "UPDATE expected_cash_events SET status = 'arrived' WHERE id = (SELECT id FROM "
                        "expected_cash_events WHERE status = 'expected' AND amount = %s ORDER BY on_date LIMIT 1)",
                        (t.amount,))
                conn.commit()
            if t.outgoing and self._jobs.enqueue("bank_txn", {"txn": t.model_dump(mode="json")}, f"txn:{t.id}"):
                n += 1
        if txns:
            last = txns[-1].booked_at
            _save(self._cf, "bank", {"since": last.isoformat(),
                                     "ids_at_since": [t.id for t in txns if t.booked_at == last]})
        return n


class MailWatcher:
    """New mail that names an invoice Scout is waiting for (number, or the supplier) -> a `reply` job."""

    def __init__(self, conn_factory: ConnFactory, connectors: Connectors, jobs: JobQueue, recorder: TaskRecorder):
        self._cf, self.connectors, self._jobs, self._rec = conn_factory, connectors, jobs, recorder

    def poll(self) -> int:
        mail = self.connectors.mail
        if not mail.available():
            return 0
        name = f"mail:{mail.name}"
        hits, cursor = mail.changes(_cursor(self._cf, name))
        _save(self._cf, name, cursor)
        waiting = [t for t in self._rec.find_open() if t.loop == "invoicing"]
        n = 0
        for h in hits:
            hay = " ".join([h.subject, h.sender, h.body[:4000], *h.attachments]).casefold()
            for task in waiting:
                number = invoice_number_in(task.title)
                supplier = (task.counterparty or "").split()[0].casefold() if task.counterparty else ""
                if not ((number and number.casefold() in hay) or (supplier and len(supplier) > 3 and supplier in hay)):
                    continue
                files = []
                for att in h.attachments:
                    if att.lower().endswith(".pdf"):
                        data = mail.attachment(h.id, att, task_id=task.id)
                        if data:
                            files.append({"name": att, "data_b64": base64.b64encode(data).decode()})
                if not files:
                    continue
                if self._jobs.enqueue("reply", {"task_id": task.id, "from_id": "email", "text": h.subject,
                                                "files": files}, f"mail:{h.id}:{task.id}"):
                    n += 1
        return n


class Scheduler:
    """Daily cost checks and weekly liquidity review at 07:00 Frankfurt time. Reminders need nothing: they are jobs
    with a future `run_after`."""

    def __init__(self, jobs: JobQueue, hour: int = 7):
        self._jobs, self._hour = jobs, hour

    def tick(self, now: datetime | None = None) -> list[str]:
        local = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
        out = []
        if local.hour >= self._hour:
            if self._jobs.enqueue("cost_check", {}, f"cost:{local.date()}"):
                out.append("cost_check")
            # Strategy news is scheduled every 24 hours by the dashboard pod.
            if local.weekday() == 0:
                year, week, _ = local.isocalendar()
                if self._jobs.enqueue("liquidity_review", {}, f"liq:{year}-W{week:02d}"):
                    out.append("liquidity_review")
        return out
