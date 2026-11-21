"""Seed Pizzeria Amalfi for Scout, in real time.

    python -m scout.seed [--reset] [--grazyna-away/--grazyna-here]

The organizer's 2025 statement is shifted by whole weeks so it ends before today and becomes the bank mock's
history and the company ledger (`transactions`, all invoices on file). The demo evidence for the live cases
(`scout.demo_data`) is planted where Scout will look: Grażyna's company folders, the local mailbox folder (and the
Gmail label, see `scout.demo seed-mail`) and the reply PDF for the "nowhere" case.
"""

from __future__ import annotations

import argparse
import random
import shutil
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from cfo.data import people
from cfo.data.invoices import build_invoice, invoice_eml, invoice_pdf, supplier_of
from cfo.data.metrics import build_months
from cfo.data.source import find_source_csv, load_bank_csv
from cfo.db.repo import Repository
from cfo.domain import CostMetric, Employee, Invoice, Transaction, Vacation
from scout import db as scout_db
from scout.config import REPO_ROOT, get_scout_settings
from scout.demo_data import LIVE_INVOICE_NUMBERS, POISON_NOTES, SCENARIOS
from scout.mock_bank.store import BankStore, week_shift

COMPANY_TABLES = ["audit_log", "agent_task_steps", "approvals", "expected_cash_events", "agent_jobs", "agent_tasks",
                  "watcher_state", "connection_credentials", "connections", "bank_mock_transactions", "access_log",
                  "rules", "cost_metrics", "invoice_lines", "transactions", "invoices", "vacations", "employees",
                  "teams", "llm_usage"]

DEFAULT_CONNECTIONS: list[dict[str, Any]] = [
    {"id": "bank", "kind": "bank", "provider": "revolut_mock", "display_name": "Revolut Business",
     "config": {"note": "mocked", "account": "acc_amalfi"}, "status": "connected"},
    {"id": "invoicing", "kind": "invoicing", "provider": "postgres", "display_name": "Invoicing system",
     "config": {"tables": ["invoices", "invoice_lines"]}, "status": "connected"},
    {"id": "ksef", "kind": "invoicing", "provider": "ksef_test", "display_name": "KSeF · test environment",
     "config": {"env": "test", "api": "https://api-test.ksef.mf.gov.pl/v2", "phase": "idle", "steps": []},
     "status": "disconnected"},
    {"id": "files", "kind": "files", "provider": "workstation", "display_name": "Company laptops",
     "config": {"folders": ["Downloads", "Mail", "Documents"], "laptops": ["grazyna"]}, "status": "connected"},
    {"id": "slack", "kind": "chat", "provider": "slack", "display_name": "Slack",
     "config": {"channels": ["finance"]}, "status": "connected"},
    {"id": "gmail", "kind": "mail", "provider": "gmail", "display_name": "Company email",
     "config": {"label": "Amalfi"}, "status": "disconnected"},
    {"id": "mail_folder", "kind": "mail", "provider": "eml_folder", "display_name": "Mail archive (local)",
     "config": {"root": "data/generated/mailbox"}, "status": "connected", "enabled": True},
]


def _remap_month(m: int, weeks: int) -> date:
    """Month `m` of 2025 -> the first day of the month its 15th lands in after the shift."""
    d = date(2025, m, 15) + timedelta(weeks=weeks)
    return d.replace(day=1)


def _budget(value: Decimal) -> Decimal:
    return (value * Decimal("1.1") / 50).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 50


def _shift_invoice(inv: Invoice, month: date, kind: str) -> Invoice:
    end = month.replace(day=monthrange(month.year, month.month)[1])
    number = f"{kind}-{month:%Y-%m}"
    return inv.model_copy(update={"id": f"inv_{kind.lower()}{month:%Y%m}", "number": number, "issue_date": end,
                                  "due_date": end + timedelta(days=15), "transaction_id": None})


def _people(today: date, weeks: int, grazyna_away: bool) -> list[Employee]:
    out = []
    for e in people.EMPLOYEES:
        vac = [Vacation(start=v.start + timedelta(weeks=weeks), end=v.end + timedelta(weeks=weeks))
               for v in e.vacations if v.start.year == 2025]
        if e.id == "grazyna":
            vac = [v for v in vac if v.end < today - timedelta(days=3)]
            if grazyna_away:
                vac.append(Vacation(start=today - timedelta(days=2), end=today + timedelta(days=10)))
        out.append(e.model_copy(update={"vacations": vac}))
    return out


def _pdf(number: str, amount: Decimal, when: date, seed: int) -> tuple[Invoice, bytes]:
    sup = supplier_of(number)
    txn = Transaction(id="tmp", booking_date=when, type="transfer", reference=number, counterparty=sup["name"],
                      amount=amount, invoice_number=number)
    inv = build_invoice(txn, random.Random(f"{seed}:demo:{number}"), transaction_id=None)
    return inv, invoice_pdf(inv, sup["address"])


def plant_demo_evidence(data_dir: Path, today: date, seed: int = 42) -> dict[str, str]:
    """Files for the live cases; returns what went where."""
    ws = data_dir / "workstations" / "grazyna"
    for sub in ("Downloads", "Mail", "Documents", "Private"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    (ws / "Private" / "personal-notes.txt").write_text("Private. Scout must never read this.\n")
    (ws / "Downloads" / "Speisekarte-2027-Entwurf.txt").write_text("Draft menu, not an invoice.\n")
    placed: dict[str, str] = {}
    files = SCENARIOS["files"]
    _, pdf = _pdf(files.invoice_number, files.amount, today, seed)
    (ws / "Downloads" / f"Invoice_{files.invoice_number}.pdf").write_bytes(pdf)
    placed["files"] = f"workstations/grazyna/Downloads/Invoice_{files.invoice_number}.pdf"

    email = SCENARIOS["email"]
    inv, pdf = _pdf(email.invoice_number, email.amount, today, seed)
    box = data_dir / "mailbox"
    box.mkdir(parents=True, exist_ok=True)
    (box / f"001-invoice-{email.invoice_number}.eml").write_bytes(invoice_eml(inv, pdf, 1))
    placed["email"] = f"mailbox/001-invoice-{email.invoice_number}.eml"

    poisoned = SCENARIOS["poisoned"]  # a supplier invoice mail whose PDF carries an instruction for the agent
    sup = supplier_of(poisoned.invoice_number)
    txn = Transaction(id="tmp", booking_date=today, type="transfer", reference=poisoned.invoice_number,
                      counterparty=sup["name"], amount=poisoned.amount, invoice_number=poisoned.invoice_number)
    inv = build_invoice(txn, random.Random(f"{seed}:demo:{poisoned.invoice_number}"), transaction_id=None)
    pdf = invoice_pdf(inv, sup["address"], notes=POISON_NOTES)
    (box / f"002-invoice-{poisoned.invoice_number}.eml").write_bytes(invoice_eml(inv, pdf, 2))
    placed["poisoned"] = f"mailbox/002-invoice-{poisoned.invoice_number}.eml"

    nowhere = SCENARIOS["nowhere"]
    _, pdf = _pdf(nowhere.invoice_number, nowhere.amount, today, seed)
    reply = data_dir / "demo"
    reply.mkdir(parents=True, exist_ok=True)
    (reply / f"{nowhere.invoice_number}.pdf").write_bytes(pdf)
    placed["nowhere_reply"] = f"demo/{nowhere.invoice_number}.pdf"
    return placed


def seed(conn_factory, *, data_dir: Path, csv: Path | None = None, reset: bool = False,  # noqa: ANN001
         grazyna_away: bool = True, today: date | None = None, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    csv = csv or find_source_csv()
    raw = load_bank_csv(csv)
    weeks = week_shift(raw[-1].booking_date, today)
    txns = [t for t in load_bank_csv(csv, shift_weeks=weeks) if t.invoice_number not in LIVE_INVOICE_NUMBERS]
    months = build_months(raw, 42)

    conn = conn_factory()
    scout_db.apply_schema(conn)
    if reset:
        conn.execute(f"TRUNCATE {', '.join(COMPANY_TABLES)} CASCADE")
        conn.execute("ALTER SEQUENCE agent_task_seq RESTART WITH 100")
        conn.commit()
    repo = Repository(conn)
    with repo.transaction():
        spend: dict[str, Decimal] = {}
        for t in txns:
            if t.team_id and t.amount < 0:
                spend[t.team_id] = spend.get(t.team_id, Decimal(0)) - t.amount
        spend["marketing"] = sum((m.ad_spend for m in months), Decimal(0))
        spend["it"] = sum((m.cloud_invoice.total for m in months), Decimal(0))
        budgets = {k: _budget(v / 12) for k, v in spend.items()}
        budgets["equipment"] = Decimal(500)
        for team in people.make_teams(budgets):
            repo.add_team(team)
        for emp in _people(today, weeks, grazyna_away):
            repo.add_employee(emp)
        for t in txns:
            repo.add_transaction(t)
        n_inv = 0
        for t in txns:
            if t.invoice_number:
                inv = build_invoice(t, random.Random(f"42:inv:{t.invoice_number}"), transaction_id=t.id)
                repo.add_invoice(inv)
                n_inv += 1
        for m in months:
            month = _remap_month(m.month.month, weeks)
            repo.add_invoice(_shift_invoice(m.cloud_invoice, month, "CLD"))
            repo.add_invoice(_shift_invoice(m.ads_invoice, month, "ADS"))
            for cm in m.cost_metrics:
                repo.add_cost_metric(CostMetric(**{**cm.model_dump(), "id": f"{cm.id.rsplit('_', 1)[0]}_{month:%Y%m}",
                                                   "month": month}))
        for c in DEFAULT_CONNECTIONS:
            if c["id"] == "mail_folder":  # absolute: the installed package has no repo root to be relative to
                c = {**c, "config": {"root": str((data_dir / "mailbox").resolve())}}
            conn.execute(
                "INSERT INTO connections (id, kind, provider, display_name, config, status, enabled) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
                (c["id"], c["kind"], c["provider"], c["display_name"], Jsonb(c["config"]), c["status"],
                 c.get("enabled", True)),
            )
        conn.execute(
            "INSERT INTO watcher_state (name, cursor) VALUES ('bank', %s) ON CONFLICT (name) DO UPDATE SET "
            "cursor = EXCLUDED.cursor, updated_at = now()",
            (Jsonb({"since": now.isoformat()}),),
        )
    conn.close()
    n_bank = BankStore(conn_factory).load_history(txns, now=now)
    return {"weeks": weeks, "transactions": len(txns), "bank_mock": n_bank, "invoices": n_inv,
            "employees": len(people.EMPLOYEES)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout.seed")
    parser.add_argument("--reset", action="store_true", help="empty company and Scout tables first")
    parser.add_argument("--data", type=Path, default=None, help="data dir (default DATA_DIR)")
    parser.add_argument("--grazyna-here", action="store_true", help="Grażyna is not on vacation")
    args = parser.parse_args(argv)
    s = get_scout_settings()
    data_dir = args.data or (REPO_ROOT / s.data_dir if not Path(s.data_dir).is_absolute() else Path(s.data_dir))
    if args.reset:
        shutil.rmtree(data_dir / "workstations", ignore_errors=True)
        shutil.rmtree(data_dir / "mailbox", ignore_errors=True)
    stats = seed(scout_db.factory(), data_dir=data_dir, reset=args.reset, grazyna_away=not args.grazyna_here)
    placed = plant_demo_evidence(data_dir, datetime.now(timezone.utc).date())
    print(f"seeded: {stats}")
    for k, v in placed.items():
        print(f"  {k}: {data_dir / v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
