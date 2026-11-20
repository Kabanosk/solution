"""The bank mock's ledger: the shifted CSV history plus payments posted live for the demo."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from scout.db import ConnFactory
from scout.models import BankTxn

CARD_TYPES = {"GIRO CARD PAYMENT", "CREDIT CARD PAYMENT", "card_payment"}


def week_shift(last_csv_date: date, today: date) -> int:
    """Smallest whole number of weeks that puts `last_csv_date` strictly before `today`."""
    weeks = max(0, (today - last_csv_date).days // 7)
    while last_csv_date + timedelta(weeks=weeks) >= today:
        weeks -= 1
    while last_csv_date + timedelta(weeks=weeks + 1) < today:
        weeks += 1
    return weeks


def _row_to_txn(r: dict) -> BankTxn:
    return BankTxn(id=r["id"], booked_at=r["booked_at"], amount=r["amount"], currency=r["currency"],
                   counterparty=r["counterparty"], reference=r["reference"],
                   type="card_payment" if r["type"] in CARD_TYPES else r["type"],
                   card_last4=r["card_last4"], holder_employee_id=r["holder_employee_id"])


class BankStore:
    def __init__(self, conn_factory: ConnFactory):
        self._cf = conn_factory

    def load_history(self, txns: list, *, now: datetime | None = None) -> int:
        """Book CSV lines (cfo `Transaction`s, already shifted) at 09:00 + n minutes; rows after `now` are hidden."""
        now = now or datetime.now(timezone.utc)
        per_day: dict[date, int] = {}
        n = 0
        with self._cf() as conn:
            for t in txns:
                k = per_day[t.booking_date] = per_day.get(t.booking_date, -1) + 1
                booked = datetime.combine(t.booking_date, time(9, 0), tzinfo=timezone.utc) + timedelta(minutes=3 * k)
                if booked > now:
                    continue
                conn.execute(
                    "INSERT INTO bank_mock_transactions (id, booked_at, type, reference, counterparty, "
                    "counterparty_account, amount, currency, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'csv') "
                    "ON CONFLICT (id) DO NOTHING",
                    (t.id, booked, t.type, t.reference, t.counterparty, t.counterparty_account, t.amount, t.currency),
                )
                n += 1
            conn.commit()
        return n

    def list(self, from_ts: datetime | None = None, to_ts: datetime | None = None, count: int = 1000) -> list[BankTxn]:
        now = datetime.now(timezone.utc)
        sql, args = "SELECT * FROM bank_mock_transactions WHERE booked_at <= %s", [to_ts or now]
        if from_ts:
            sql += " AND booked_at > %s"
            args.append(from_ts)
        sql += " ORDER BY booked_at, id LIMIT %s"
        args.append(count)
        with self._cf() as conn:
            return [_row_to_txn(r) for r in conn.execute(sql, args).fetchall()]

    def post(self, counterparty: str, amount: Decimal, reference: str, *, type: str = "transfer",
             holder_employee_id: str | None = None, card_last4: str | None = None) -> BankTxn:
        booked = datetime.now(timezone.utc)
        digest = hashlib.sha1(f"{booked.isoformat()}|{counterparty}|{amount}|{reference}".encode()).hexdigest()
        with self._cf() as conn:
            row = conn.execute(
                "INSERT INTO bank_mock_transactions (id, booked_at, type, reference, counterparty, amount, currency, "
                "card_last4, holder_employee_id, source) VALUES (%s,%s,%s,%s,%s,%s,'EUR',%s,%s,'live') RETURNING *",
                (f"tx_{digest[:12]}", booked, type, reference, counterparty, Decimal(amount), card_last4,
                 holder_employee_id),
            ).fetchone()
            conn.commit()
        return _row_to_txn(row)

    def balance(self) -> Decimal:
        with self._cf() as conn:
            row = conn.execute("SELECT coalesce(sum(amount), 0) AS b FROM bank_mock_transactions WHERE booked_at <= now()").fetchone()
        return Decimal(row["b"])
