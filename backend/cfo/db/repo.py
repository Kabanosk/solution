"""Small repository over Postgres: domain models in, domain models out. Sync psycopg 3.

    with Repository.connect() as repo:
        repo.add_transaction(txn)
        repo.list_transactions(undocumented_only=True)

Each write commits immediately. Later tasks add queries here as they need them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any, Self

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from cfo.config import get_settings
from cfo.domain import (
    CostMetric,
    Employee,
    Invoice,
    InvoiceLine,
    Rule,
    Team,
    Transaction,
    Vacation,
)


def _insert(
    table: str, row: dict[str, Any], *, upsert_key: str | None = "id", keep: frozenset[str] = frozenset()
) -> tuple[str, list[Any]]:
    """INSERT ... ON CONFLICT upsert. Columns in `keep` are written on insert but never touched on conflict."""
    cols = list(row)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    if upsert_key:
        keys = {k.strip() for k in upsert_key.split(",")}
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys and c not in keep)
        sql += f" ON CONFLICT ({upsert_key}) DO UPDATE SET {updates}"
    return sql, [Jsonb(v) if isinstance(v, dict) else v for v in row.values()]


class Repository:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self._tx_depth = 0

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes into one database transaction: nothing commits until the outermost block ends, and an
        exception rolls everything back. Nestable."""
        self._tx_depth += 1
        try:
            yield
        except BaseException:
            if self._tx_depth == 1:
                self.conn.rollback()
            raise
        else:
            if self._tx_depth == 1:
                self.conn.commit()
        finally:
            self._tx_depth -= 1

    def _commit(self) -> None:
        if self._tx_depth == 0:
            self.conn.commit()

    @classmethod
    def connect(cls, database_url: str | None = None) -> Self:
        from cfo.tenancy import connect
        return cls(connect(database_url or get_settings().database_url))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.conn.close()

    def _write(self, table: str, row: dict[str, Any], upsert_key: str | None = "id") -> None:
        sql, params = _insert(table, row, upsert_key=upsert_key)
        self.conn.execute(sql, params)
        self._commit()

    def _all(self, sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return self.conn.execute(sql, params).fetchall()

    # -- teams, employees ----------------------------------------------------------------------
    def add_team(self, team: Team) -> None:
        self._write("teams", team.model_dump())

    def list_teams(self) -> list[Team]:
        return [Team(**r) for r in self._all("SELECT * FROM teams ORDER BY id")]

    def add_employee(self, emp: Employee) -> None:
        """Upsert an employee and replace their vacations. Managers must be added before reports."""
        row = emp.model_dump(exclude={"vacations"})
        sql, params = _insert("employees", row)
        self.conn.execute(sql, params)
        self.conn.execute("DELETE FROM vacations WHERE employee_id = %s", (emp.id,))
        for v in emp.vacations:
            self.conn.execute(
                "INSERT INTO vacations (employee_id, start_date, end_date) VALUES (%s, %s, %s)",
                (emp.id, v.start, v.end),
            )
        self._commit()

    def _employees(self, where: str = "", params: tuple[Any, ...] = ()) -> list[Employee]:
        vac: dict[str, list[Vacation]] = {}
        for r in self._all("SELECT employee_id, start_date, end_date FROM vacations ORDER BY start_date"):
            vac.setdefault(r["employee_id"], []).append(Vacation(start=r["start_date"], end=r["end_date"]))
        rows = self._all(f"SELECT * FROM employees {where} ORDER BY id", params)
        return [Employee(**r, vacations=vac.get(r["id"], [])) for r in rows]

    def list_employees(self) -> list[Employee]:
        return self._employees()

    def get_employee(self, employee_id: str) -> Employee | None:
        found = self._employees("WHERE id = %s", (employee_id,))
        return found[0] if found else None

    # -- transactions, invoices ----------------------------------------------------------------
    def add_transaction(self, txn: Transaction) -> None:
        """Upsert a bank line. Re-adding (replay, reseed) never resets `documented` / `invoice_id`: the
        reconciliation state is owned by `add_invoice`."""
        sql, params = _insert("transactions", txn.model_dump(), keep=frozenset({"documented", "invoice_id"}))
        self.conn.execute(sql, params)
        self._commit()

    def get_transaction(self, txn_id: str) -> Transaction | None:
        rows = self._all("SELECT * FROM transactions WHERE id = %s", (txn_id,))
        return Transaction(**rows[0]) if rows else None

    def list_transactions(
        self,
        *,
        since: date | None = None,
        until: date | None = None,
        undocumented_only: bool = False,
    ) -> list[Transaction]:
        """Newest first. `undocumented_only`: payments carrying an invoice number with no Invoice yet."""
        where, params = ["TRUE"], []
        if since:
            where.append("booking_date >= %s")
            params.append(since)
        if until:
            where.append("booking_date <= %s")
            params.append(until)
        if undocumented_only:
            where.append("invoice_number IS NOT NULL AND NOT documented")
        sql = f"SELECT * FROM transactions WHERE {' AND '.join(where)} ORDER BY booking_date DESC, id"
        return [Transaction(**r) for r in self._all(sql, params)]

    def lock_invoice_number(self, number: str) -> None:
        """Serialise writers of one invoice number until the transaction ends (also covers a number that has
        no row yet). Reentrant within a session."""
        self.conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"invoice:{number}",))

    def lock_transaction(self, txn_id: str) -> Transaction | None:
        """Read a bank line `FOR UPDATE`: the row stays locked until the current transaction ends."""
        rows = self._all("SELECT * FROM transactions WHERE id = %s FOR UPDATE", (txn_id,))
        return Transaction(**rows[0]) if rows else None

    def add_invoice(self, inv: Invoice, *, commit: bool = True) -> None:
        """Upsert an invoice with its lines and, if `transaction_id` is set, mark that payment documented.

        Keeps the two sides consistent: an invoice that moves to another payment un-documents the previous one,
        and re-adding an invoice that does not carry a link keeps the existing link. `commit=False` leaves the
        transaction open so the caller can wrap the check and the write in one (locked) transaction.
        """
        self.lock_invoice_number(inv.number)
        prev = self._all("SELECT transaction_id FROM invoices WHERE id = %s FOR UPDATE", (inv.id,))
        previous_txn = prev[0]["transaction_id"] if prev else None
        if inv.transaction_id is None and previous_txn:
            inv = inv.model_copy(update={"transaction_id": previous_txn})
        touched = sorted({t for t in (previous_txn, inv.transaction_id) if t})
        if touched:  # payment rows in id order, after the invoice lock: one lock order, no deadlocks
            self.conn.execute("SELECT id FROM transactions WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (touched,))
        sql, params = _insert("invoices", inv.model_dump(exclude={"lines"}))
        self.conn.execute(sql, params)
        self.conn.execute("DELETE FROM invoice_lines WHERE invoice_id = %s", (inv.id,))
        for pos, line in enumerate(inv.lines):
            self.conn.execute(
                "INSERT INTO invoice_lines (invoice_id, position, description, quantity, unit_price, amount, category)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (inv.id, pos, line.description, line.quantity, line.unit_price, line.amount, line.category),
            )
        if previous_txn and previous_txn != inv.transaction_id:
            self.conn.execute(
                "UPDATE transactions SET documented = FALSE, invoice_id = NULL WHERE id = %s AND invoice_id = %s",
                (previous_txn, inv.id),
            )
        if inv.transaction_id:
            self.conn.execute(
                "UPDATE transactions SET documented = TRUE, invoice_id = %s WHERE id = %s",
                (inv.id, inv.transaction_id),
            )
        if commit:
            self._commit()

    def get_invoice_by_number(self, number: str) -> Invoice | None:
        rows = self._all("SELECT * FROM invoices WHERE number = %s", (number,))
        if not rows:
            return None
        lines = self._all(
            "SELECT description, quantity, unit_price, amount, category FROM invoice_lines"
            " WHERE invoice_id = %s ORDER BY position",
            (rows[0]["id"],),
        )
        return Invoice(**rows[0], lines=[InvoiceLine(**ln) for ln in lines])

    # -- cost metrics, events ------------------------------------------------------------------
    def add_cost_metric(self, cm: CostMetric) -> None:
        self._write("cost_metrics", cm.model_dump(mode="json"), upsert_key="kind, month")

    def list_cost_metrics(self, kind: str | None = None) -> list[CostMetric]:
        sql = "SELECT * FROM cost_metrics" + (" WHERE kind = %s" if kind else "") + " ORDER BY month"
        return [CostMetric(**r) for r in self._all(sql, [kind] if kind else [])]

    # -- rules -----------------------------------------------------------------------------------
    def add_rule(self, rule: Rule) -> None:
        self._write("rules", rule.model_dump(mode="json"))

    def list_rules(self, active_only: bool = True) -> list[Rule]:
        sql = "SELECT * FROM rules" + (" WHERE active" if active_only else "") + " ORDER BY created_at DESC"
        return [Rule(**r) for r in self._all(sql)]
