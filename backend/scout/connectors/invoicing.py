"""Invoicing system of record: the company's `invoices` tables (Postgres, treated as a real system)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from cfo.db.repo import Repository
from cfo.domain import Invoice, InvoiceLine
from scout.audit import Audit
from scout.db import ConnFactory
from scout.models import InvoiceDoc


class InvoicingConnector(Protocol):
    name: str

    def find(self, *, number: str | None = None, supplier: str | None = None, amount: Decimal | None = None,
             around: date | None = None, days: int = 7, task_id: str | None = None) -> list[InvoiceDoc]: ...

    def file(self, doc: InvoiceDoc, transaction_id: str, *, task_id: str | None = None) -> tuple[bool, str]: ...


class PostgresInvoicing:
    name = "invoicing_pg"
    label = "invoicing system"

    def __init__(self, conn_factory: ConnFactory, audit: Audit):
        self._cf = conn_factory
        self._audit = audit

    def find(self, *, number=None, supplier=None, amount=None, around=None, days=7, task_id=None):  # noqa: ANN001
        where, args = [], []
        if number:
            where.append("upper(number) = upper(%s)")
            args.append(number)
        else:
            if supplier:
                where.append("supplier ILIKE %s")
                args.append(f"%{supplier.split()[0]}%")
            if amount is not None:
                where.append("total = %s")
                args.append(abs(Decimal(amount)))
            if around:
                where.append("issue_date BETWEEN %s AND %s")
                args += [around - timedelta(days=days), around + timedelta(days=days)]
        if not where:
            return []
        with self._cf() as conn:
            rows = conn.execute(f"SELECT * FROM invoices WHERE {' AND '.join(where)} ORDER BY issue_date DESC LIMIT 20",
                                args).fetchall()
        self._audit.record("agent", "search", "invoicing", f"number={number} supplier={supplier} amount={amount}",
                           task_id=task_id)
        return [InvoiceDoc(number=r["number"], supplier=r["supplier"], issue_date=r["issue_date"], total=r["total"],
                           source="db", invoice_id=r["id"], transaction_id=r["transaction_id"]) for r in rows]

    def file(self, doc: InvoiceDoc, transaction_id: str, *, task_id: str | None = None) -> tuple[bool, str]:
        """Store the invoice and link it to the payment. Errors: amount_mismatch | already_documented |
        linked_elsewhere | unknown_transaction."""
        conn = self._cf()
        repo = Repository(conn)
        try:
            with repo.transaction():
                repo.lock_invoice_number(doc.number)
                txn = repo.lock_transaction(transaction_id)
                if txn is None:
                    return False, "unknown_transaction"
                if txn.documented:
                    return False, "already_documented"
                if abs(txn.amount) != doc.total:
                    return False, "amount_mismatch"
                existing = repo.get_invoice_by_number(doc.number)
                if existing and existing.transaction_id and existing.transaction_id != transaction_id:
                    return False, "linked_elsewhere"
                inv = Invoice(
                    id=existing.id if existing else "inv_" + doc.number.replace("-", "").lower(),
                    number=doc.number, supplier=doc.supplier, issue_date=doc.issue_date, total=doc.total,
                    transaction_id=transaction_id, source=doc.source.split(":", 1)[0], category=txn.category,
                    team_id=txn.team_id,
                    lines=[InvoiceLine(**ln) for ln in doc.lines] if doc.lines else (existing.lines if existing else []),
                )
                repo.add_invoice(inv, commit=False)
        finally:
            conn.close()
        self._audit.record("agent", "write", "invoicing", f"file {doc.number} -> {transaction_id}", task_id=task_id)
        return True, inv.id
