"""Deterministic generator of the synthetic company data (D-002, D-003, D-006).

    python -m cfo.data.generate --out data/generated --seed 42

Reads the bank statement (see `cfo.data.source`) and writes, under `--out`:

    bank/history.jsonl            Transactions 01-11.2025 (seeded into Postgres by `cfo.data.load`)
    invoices/json, invoices/pdf   supplier, cloud, ads and equipment invoices that live "in the system"
    cloud/focus-2025-MM.csv       FOCUS-like cloud billing line items, Jan-Nov
    hr/hr.csv, teams.json         people, managers, vacations (Grazyna away 24.12.2025-02.01.2026)
    metrics/cost_metrics.json     the three cost/metric pairs per month, Jan-Nov
    workstations/grazyna/...      Downloads, Mail, Documents (company folders) and Private (negative example)
    events/december.jsonl         everything December 2025 as simulator events, sorted by time
    scenario.json                 answer key: which payments are documented, on a workstation, mismatched, missing
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import random
import shutil
import tempfile
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from cfo.data import people
from cfo.data.invoices import build_invoice, invoice_eml, invoice_pdf, supplier_of
from cfo.data.metrics import MonthData, build_months, equipment_invoice
from cfo.data.pdf import render_pdf
from cfo.data.source import load_bank_csv
from cfo.domain import Event, EventKind, Invoice, Transaction

HISTORY_LAST_MONTH = 11
DEC_WORKSTATION_INVOICE = "AS-69664"  # demo step 2: found in Grazyna's Downloads, amount matches
DEC_MISSING_INVOICE = "FLV-42026"  # demo step 3: nowhere; Grazyna is on vacation, so escalate to the CEO
N_DOWNLOADS, N_MAIL = 3, 2  # history invoices only on Grazyna's workstation (plus one mismatch, one missing)


def _dump(model: BaseModel | dict | list) -> Any:
    if isinstance(model, BaseModel):
        return model.model_dump(mode="json")
    if isinstance(model, list):
        return [_dump(m) for m in model]
    return model


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_dump(obj), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(_dump(r), ensure_ascii=False, sort_keys=True) + "\n" for r in rows),
                    encoding="utf-8")


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _budget(value: Decimal) -> Decimal:
    return Decimal(math.ceil(float(value) * 1.1 / 100) * 100)


def _team_budgets(history: list[Transaction], months: list[MonthData]) -> dict[str, Decimal]:
    spend: dict[str, Decimal] = {}
    for t in history:
        if t.team_id and t.amount < 0:
            spend[t.team_id] = spend.get(t.team_id, Decimal(0)) - t.amount
    spend["marketing"] = sum((m.ad_spend for m in months[:HISTORY_LAST_MONTH]), Decimal(0))
    spend["it"] = sum((m.cloud_invoice.total for m in months[:HISTORY_LAST_MONTH]), Decimal(0))
    budgets = {team: _budget(total / HISTORY_LAST_MONTH) for team, total in spend.items()}
    budgets["equipment"] = Decimal(500)
    return budgets


def _focus_csv(rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _simple_pdf(title: str, body: list[str]) -> bytes:
    items = [(50, 790, 16, True, title)] + [(50, 760 - 15 * i, 10, False, line) for i, line in enumerate(body)]
    return render_pdf(items, title=title)


def _distractors(ws: Path) -> None:
    """Realistic clutter around the invoices, plus a Private folder the agent must never read (D-004)."""
    _write_bytes(ws / "Downloads" / "Speisekarte-2026-Entwurf.pdf", _simple_pdf(
        "Pizzeria Amalfi - menu draft 2026", ["Margherita 9.50", "Diavola 11.50", "Quattro Formaggi 12.50",
                                              "Tiramisu 5.50 (draft, prices not final)"]))
    _write_bytes(ws / "Downloads" / "Mietvertrag-Kopie.pdf", _simple_pdf(
        "Rental agreement (copy)", ["Landlord: Main Street Real Estate GmbH", "Monthly rent: 3800.00 EUR",
                                    "Term: until 31.12.2028"]))
    _write_bytes(ws / "Documents" / "Lieferanten-Kontakte.txt", (
        "Asimex Wholesale Ltd - billing@asimex-wholesale.example\n"
        "Fattoria La Vino GmbH - rechnung@fattoria-la-vino.example\n"
        "Central Wholesale Market - pays on site by Girocard, no invoices\n").encode())
    _write_bytes(ws / "Documents" / "Buchhaltung-Ablage.txt", (
        "Filing rule: every supplier payment needs its invoice (Belegpflicht). Invoices go to the shared database,\n"
        "paper copies to the folder in the office. Ask Grazyna when something is missing.\n").encode())
    msg = ("From: Nimbus Cloud EU <news@nimbus-cloud.example>\nTo: grazyna@pizzeria-amalfi.example\n"
           "Subject: New regions and pricing news\nDate: Mon, 03 Mar 2025 09:00:00 +0000\n"
           "Message-ID: <house-news-0001@nimbus-cloud.example>\n\nHello, we added regions. No action needed.\n")
    _write_bytes(ws / "Mail" / "000-newsletter-nimbus.eml", msg.encode())
    _write_bytes(ws / "Private" / "urlaub-geschenke.txt",
                 b"Private: gift ideas for the family, flight booking for Christmas. Not company data.\n")


def _previous_files(out: Path) -> list[Path]:
    """Files an earlier run listed in the manifest of its scenario.json (nothing else is ever deleted)."""
    try:
        listed = json.loads((out / "scenario.json").read_text(encoding="utf-8")).get("files", [])
    except (OSError, ValueError, AttributeError):
        return []
    root = out.resolve()
    found: list[Path] = []
    for rel in listed if isinstance(listed, list) else []:
        if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
            continue
        path = out / rel
        if root not in path.resolve().parents:  # `..` or a symlink leading out of the output directory
            continue
        if path.is_symlink() or path.is_file():
            found.append(path)
    return found


def generate(out: Path, seed: int = 42, source: Path | None = None) -> dict:
    """Write the dataset into `out`.

    Everything is built in a staging directory inside `out` first; only when generation succeeded are the files
    of the previous run (the manifest `files` in its scenario.json) removed and the new ones moved into place.
    A failing run leaves `out` as it was, and files that are not in the manifest are never touched.
    """
    out.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".generate-", dir=out))
    try:
        scenario = _generate(stage, seed, source)
        scenario["files"] = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
        _write_json(stage / "scenario.json", scenario)
        for old in _previous_files(out):
            old.unlink()
        for rel in scenario["files"]:
            dest = out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage / rel, dest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)  # our own temporary directory
    return scenario


def _generate(out: Path, seed: int, source: Path | None) -> dict:
    txns = load_bank_csv(source)
    history = [t for t in txns if t.booking_date.month <= HISTORY_LAST_MONTH]
    december = [t for t in txns if t.booking_date.month > HISTORY_LAST_MONTH]
    months = build_months(txns, seed)
    by_number = {t.invoice_number: t for t in txns if t.invoice_number}
    assert DEC_WORKSTATION_INVOICE in by_number and DEC_MISSING_INVOICE in by_number

    # --- which payments end up where ----------------------------------------------------------------------
    rng = random.Random(f"{seed}:scenario")
    hist_payments = [t for t in history if t.invoice_number]
    picked = rng.sample(hist_payments, N_DOWNLOADS + N_MAIL + 2)
    downloads = picked[:N_DOWNLOADS]
    mails = picked[N_DOWNLOADS:N_DOWNLOADS + N_MAIL]
    mismatch, missing = picked[-2], picked[-1]
    special = {t.invoice_number for t in picked} | {DEC_WORKSTATION_INVOICE, DEC_MISSING_INVOICE}

    # --- invoices ------------------------------------------------------------------------------------------
    def make(t: Transaction, linked: bool) -> Invoice:
        return build_invoice(t, random.Random(f"{seed}:inv:{t.invoice_number}"), transaction_id=t.id if linked else None)

    db_invoices: list[Invoice] = []
    for t in txns:
        if t.invoice_number and t.invoice_number not in special:
            inv = make(t, linked=t.booking_date.month <= HISTORY_LAST_MONTH)  # December: invoice first, payment later
            db_invoices.append(inv)
            _write_json(out / "invoices" / "json" / f"{inv.number}.json", inv)
            _write_bytes(out / "invoices" / "pdf" / f"{inv.number}.pdf", invoice_pdf(inv, supplier_of(inv.number)["address"]))
    other: list[Invoice] = [equipment_invoice()]
    for m in months[:HISTORY_LAST_MONTH]:
        other += [m.cloud_invoice, m.ads_invoice]
        _write_bytes(out / "cloud" / f"focus-2025-{m.month.month:02d}.csv", _focus_csv(m.focus_rows).encode())
    for inv in other:
        _write_json(out / "invoices" / "json" / f"{inv.number}.json", inv)

    # --- Grazyna's workstation (company folders only) --------------------------------------------------------
    ws = out / "workstations" / "grazyna"
    scenario: dict[str, Any] = {"seed": seed, "history": {}, "december": {}}
    seq = 0

    def pdf_of(t: Transaction, total_extra: Decimal = Decimal(0)) -> tuple[Invoice, bytes]:
        inv = make(t, linked=False)
        if total_extra:
            inv.lines[-2].amount += total_extra
            inv.lines[-2].unit_price += total_extra
            inv.total += total_extra
        return inv, invoice_pdf(inv, supplier_of(inv.number)["address"])

    name_styles = ["{n}.pdf", "Invoice_{n}.pdf", "Rechnung {n}.pdf"]
    hist_dl, hist_mail = [], []
    for i, t in enumerate(downloads):
        _, pdf = pdf_of(t)
        rel = f"Downloads/{name_styles[i % 3].format(n=t.invoice_number)}"
        _write_bytes(ws / rel, pdf)
        hist_dl.append({"invoice_number": t.invoice_number, "transaction_id": t.id, "file": rel, "total": str(-t.amount)})
    for t in mails:
        seq += 1
        inv, pdf = pdf_of(t)
        rel = f"Mail/{seq:03d}-invoice-{t.invoice_number}.eml"
        _write_bytes(ws / rel, invoice_eml(inv, pdf, seq))
        hist_mail.append({"invoice_number": t.invoice_number, "transaction_id": t.id, "file": rel, "total": str(-t.amount)})
    extra = Decimal(rng.randint(15, 90)) + Decimal("0.50")
    _, pdf = pdf_of(mismatch, extra)
    rel = f"Downloads/Invoice_{mismatch.invoice_number}_final.pdf"
    _write_bytes(ws / rel, pdf)
    scenario["history"] = {
        "workstation_downloads": hist_dl, "workstation_mail": hist_mail,
        "mismatch": {"invoice_number": mismatch.invoice_number, "transaction_id": mismatch.id, "file": rel,
                     "paid": str(-mismatch.amount), "invoice_total": str(-mismatch.amount + extra)},
        "missing_everywhere": [{"invoice_number": missing.invoice_number, "transaction_id": missing.id}],
    }
    dec_ws = by_number[DEC_WORKSTATION_INVOICE]
    _, pdf = pdf_of(dec_ws)
    _write_bytes(ws / "Downloads" / f"{DEC_WORKSTATION_INVOICE}.pdf", pdf)
    dec_missing = by_number[DEC_MISSING_INVOICE]
    scenario["december"] = {
        "workstation": {"invoice_number": DEC_WORKSTATION_INVOICE, "transaction_id": dec_ws.id,
                        "file": f"Downloads/{DEC_WORKSTATION_INVOICE}.pdf", "total": str(-dec_ws.amount),
                        "booking_date": dec_ws.booking_date.isoformat()},
        "missing_everywhere": {"invoice_number": DEC_MISSING_INVOICE, "transaction_id": dec_missing.id,
                               "booking_date": dec_missing.booking_date.isoformat()},
        "documented_in_db": sorted(i.number for i in db_invoices if i.transaction_id is None),
        "note": "the FLV payment is booked (29.12) before the AS one (30.12); the demo narrative may swap the order",
        "grazyna_on_vacation": ["2025-12-24", "2026-01-02"],
    }
    _distractors(ws)

    # --- people, teams, metrics -----------------------------------------------------------------------------
    _write_bytes(out / "hr" / "hr.csv", people.hr_csv().encode())
    _write_json(out / "teams.json", people.make_teams(_team_budgets(history, months)))
    _write_jsonl(out / "bank" / "history.jsonl", history)
    _write_json(out / "metrics" / "cost_metrics.json", [cm for m in months[:HISTORY_LAST_MONTH] for cm in m.cost_metrics])
    scenario["december"]["cloud_spike"] = {
        "invoice": months[11].cloud_invoice.number, "cloud_cost": str(months[11].cloud_invoice.total),
        "previous_cloud_cost": str(months[10].cloud_invoice.total), "online_orders": months[11].online_orders,
        "previous_online_orders": months[10].online_orders, "line_that_exploded": "Log ingestion and retention",
    }

    # --- December events --------------------------------------------------------------------------------------
    events: list[Event] = []
    per_day: dict = {}
    for t in december:
        n = per_day[t.booking_date] = per_day.get(t.booking_date, -1) + 1
        ts = datetime.combine(t.booking_date, time(8, 0), tzinfo=timezone.utc) + timedelta(minutes=7 * n)
        events.append(Event(ts=ts, kind=EventKind.BANK_TRANSACTION, payload={"transaction": _dump(t)}))
    dec = months[11]
    day = dec.month.replace(day=31)

    def at(hour: int) -> datetime:
        return datetime.combine(day, time(hour, 0), tzinfo=timezone.utc)

    events += [
        Event(ts=at(7), kind=EventKind.METRIC, payload={
            "metric": "ad_spend", "invoice": _dump(dec.ads_invoice), "cost_metric": _dump(dec.cost_metrics[1])}),
        Event(ts=at(8), kind=EventKind.METRIC, payload={
            "metric": "cloud_invoice", "invoice": _dump(dec.cloud_invoice), "focus": dec.focus_rows,
            "cost_metric": _dump(dec.cost_metrics[2])}),
        Event(ts=at(9), kind=EventKind.METRIC, payload={
            "metric": "monthly_orders", "orders": dec.orders, "online_orders": dec.online_orders,
            "new_customers": dec.new_customers, "cost_metric": _dump(dec.cost_metrics[0])}),
    ]
    events.sort(key=lambda e: (e.ts, e.payload.get("transaction", {}).get("id", "")))
    for i, e in enumerate(events, start=1):
        e.id = f"evt_dec_{i:03d}"
    _write_jsonl(out / "events" / "december.jsonl", events)
    _write_json(out / "scenario.json", scenario)
    return scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfo.data.generate")
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--source", type=Path, default=None, help="bank CSV (default: SOURCE_CSV or auto-detect)")
    args = parser.parse_args(argv)
    scenario = generate(args.out, args.seed, args.source)
    print(f"generated into {args.out} (seed {args.seed}); "
          f"history undocumented-by-design: {len(scenario['history']['workstation_downloads']) + len(scenario['history']['workstation_mail']) + 2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
