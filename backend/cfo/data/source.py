"""Read the organizer's bank statement CSV into domain `Transaction`s.

The CSV is git-excluded organizer material ("for business use only"); it is never copied into the repo.
Lookup order: `SOURCE_CSV` env var, `data/source/<file>` in the repo, then `challenge/sample-data/<file>`
in the repo or any parent directory (task worktrees sit inside the main checkout).

Amounts are taken exactly as booked on the statement (the 12 Swiss tranches are -4166.67 as printed, with
the CHF figure on an EUR account), so balances match the organizer's liquidity chart.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from cfo.config import REPO_ROOT
from cfo.domain import Transaction

CSV_NAME = "pizzeria-amalfi-bank-statement-2025-v6.csv"
INVOICE_RE = re.compile(r"\b((?:AS|FLV|CWM|PFB)-\d+)\b")

SUPPLIER_CATEGORY = {
    "Asimex Wholesale Ltd": ("food", "kitchen"),
    "Central Wholesale Market": ("food", "kitchen"),
    "Fattoria La Vino GmbH": ("beverages", "kitchen"),
    "Pasta Fresca Bologna S.r.l.": ("food", "kitchen"),
    "Main Street Real Estate GmbH": ("rent", "front"),
    "City Energy & Utilities AG": ("utilities", "front"),
}
SALARY_TEAM = {
    "Vincenzo Amalfi": "front",
    "Yusuf Akcit": "kitchen",
    "Rosa Pavese": "kitchen",
    "Vanessa Paradi": "front",
}


class SourceNotFound(FileNotFoundError):
    pass


def find_source_csv() -> Path:
    candidates: list[Path] = []
    if os.environ.get("SOURCE_CSV"):
        candidates.append(Path(os.environ["SOURCE_CSV"]))
    candidates.append(REPO_ROOT / "data" / "source" / CSV_NAME)
    for parent in [REPO_ROOT, *REPO_ROOT.parents]:
        candidates.append(parent / "challenge" / "sample-data" / CSV_NAME)
    for path in candidates:
        if path.is_file():
            return path
    raise SourceNotFound(f"bank statement CSV not found; set SOURCE_CSV (tried {len(candidates)} locations)")


def _category(txn_type: str, party: str, reference: str) -> tuple[str | None, str | None]:
    if party in SUPPLIER_CATEGORY:
        return SUPPLIER_CATEGORY[party]
    if txn_type in ("CARD SETTLEMENT (DEBIT)", "CREDIT CARD PAYOUT"):
        return "revenue", None
    if txn_type == "SALARY PAYMENT":
        return "payroll", SALARY_TEAM.get(party)
    if party.startswith("Techniker"):
        return "social_security", None
    if party.startswith("Tax Office"):
        return "tax", None
    if txn_type == "INTERNATIONAL WIRE TRANSFER":
        return "partner_investment", None
    return None, None


def _day(text: str) -> date:
    return datetime.strptime(text, "%d.%m.%y").date()


def load_bank_csv(path: Path | None = None, *, shift_weeks: int = 0) -> list[Transaction]:
    """All statement lines in chronological order (the file is newest-first) with deterministic ids.

    `shift_weeks` moves every date by whole weeks (weekdays stay right), so the 2025 statement can serve as
    recent history in real time (Scout bank mock)."""
    shift = timedelta(weeks=shift_weeks)
    path = path or find_source_csv()
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    rows.reverse()
    txns: list[Transaction] = []
    for n, row in enumerate(rows, start=1):
        reference = row["Payment Reference"]
        match = INVOICE_RE.search(reference)
        category, team = _category(row["Transaction Type"], row["Beneficiary/Payer"], reference)
        txns.append(
            Transaction(
                id=f"txn_{n:04d}",
                booking_date=_day(row["Booking Date"]) + shift,
                value_date=_day(row["Value Date"]) + shift if row["Value Date"] else None,
                type=row["Transaction Type"],
                reference=reference,
                counterparty=row["Beneficiary/Payer"],
                counterparty_account=row["Counterparty Account"] or None,
                amount=Decimal(row["Amount"].replace(".", "").replace(",", ".")),
                currency="EUR",
                invoice_number=match.group(1) if match else None,
                category=category,
                team_id=team,
            )
        )
    return txns
