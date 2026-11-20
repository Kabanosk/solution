"""Seed Postgres from generated data, and apply simulator events.

    python -m cfo.data.load --data data/generated [--reset]

`seed` loads history 01-11.2025: teams, employees + vacations (from the HR CSV), bank transactions, invoices
(payments with a matching invoice become documented), cost/metric pairs. December lives in
`events/december.jsonl`; the simulator feeds each event to `apply_event`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cfo.config import get_settings
from cfo.data.people import parse_hr_csv
from cfo.db.migrate import apply_schema
from cfo.db.repo import Repository
from cfo.domain import CostMetric, Event, EventKind, Invoice, Team, Transaction

RESET_TABLES = [
    "access_log",
    "approval_requests",
    "decisions",
    "rules",
    "events",
    "cost_metrics",
    "invoice_lines",
    "transactions",
    "invoices",
    "vacations",
    "employees",
    "teams",
]


def reset(repo: Repository) -> None:
    repo.conn.execute(f"TRUNCATE {', '.join(RESET_TABLES)} CASCADE")
    repo.conn.commit()


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _in_manager_order(employees: list) -> list:
    done, ordered, pending = set(), [], list(employees)
    while pending:
        ready = [e for e in pending if e.manager_id is None or e.manager_id in done]
        if not ready:
            raise ValueError("manager cycle in HR data")
        for e in ready:
            ordered.append(e)
            done.add(e.id)
            pending.remove(e)
    return ordered


def seed(repo: Repository, data_dir: Path) -> dict[str, int]:
    """Load history into the database. Idempotent (upserts); use `reset` first for a clean demo state."""
    for team in json.loads((data_dir / "teams.json").read_text(encoding="utf-8")):
        repo.add_team(Team(**team))
    employees = parse_hr_csv((data_dir / "hr" / "hr.csv").read_text(encoding="utf-8"))
    for emp in _in_manager_order(employees):
        repo.add_employee(emp)
    txns = [Transaction(**row) for row in _jsonl(data_dir / "bank" / "history.jsonl")]
    for t in txns:
        repo.add_transaction(t)
    invoices = [
        Invoice(**json.loads(p.read_text(encoding="utf-8")))
        for p in sorted((data_dir / "invoices" / "json").glob("*.json"))
    ]
    for inv in invoices:
        repo.add_invoice(inv)
    metrics = [
        CostMetric(**row)
        for row in json.loads((data_dir / "metrics" / "cost_metrics.json").read_text())
    ]
    for cm in metrics:
        repo.add_cost_metric(cm)
    return {
        "teams": len(repo.list_teams()),
        "employees": len(employees),
        "transactions": len(txns),
        "invoices": len(invoices),
        "cost_metrics": len(metrics),
    }


def load_events(data_dir: Path) -> list[Event]:
    return [Event(**row) for row in _jsonl(data_dir / "events" / "december.jsonl")]


def ingest_transaction(repo: Repository, txn: Transaction) -> Transaction:
    """Book a new bank line. If an invoice with the payment's number is already in the database and its total
    equals the payment, link it (the payment becomes documented); otherwise the payment stays undocumented.
    """
    repo.add_transaction(txn)
    if txn.invoice_number:
        inv = repo.get_invoice_by_number(txn.invoice_number)
        if inv and inv.total == -txn.amount and not inv.transaction_id:
            inv.transaction_id = txn.id
            repo.add_invoice(inv)
    return repo.get_transaction(txn.id)


def apply_event(repo: Repository, event: Event) -> None:
    """Apply what a simulator event means for the database (the agent reacts to the event separately)."""
    repo.add_event(event)
    if event.kind == EventKind.BANK_TRANSACTION:
        ingest_transaction(repo, Transaction(**event.payload["transaction"]))
    elif event.kind == EventKind.METRIC:
        if "invoice" in event.payload:
            repo.add_invoice(Invoice(**event.payload["invoice"]))
        repo.add_cost_metric(CostMetric(**event.payload["cost_metric"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfo.data.load")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument(
        "--reset", action="store_true", help="empty all tables before seeding"
    )
    args = parser.parse_args(argv)
    apply_schema(get_settings().database_url)
    with Repository.connect() as repo:
        if args.reset:
            reset(repo)
        counts = seed(repo, args.data)
    print("seeded:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
