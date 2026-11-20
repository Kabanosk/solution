"""Connect KSeF in one click: sign in, (TE) let the suppliers issue their e-invoices, pull ours, match payments.

The run is a worker job (`ksef_connect`); its progress lives in `connections.config` of the `ksef` row, which the
dashboard polls:

    {"env": "test", "api": ..., "nip": "5621254863", "company": ..., "phase": "idle|running|done|error",
     "steps": [{"at", "text", "ok"}], "issued", "received", "imported", "linked", "linked_total", "invoices": [...]}

KSeF TE starts empty for a random NIP, so on the test environment the run first plays the suppliers: every
payment that carries an invoice number but has no invoice yet gets its e-invoice issued in KSeF by a simulated
supplier (own random NIP, own self-signed seal), plus a few already-filed invoices so the register looks real.
The demo payments (`scout.demo_data.LIVE_INVOICE_NUMBERS`) are left out, so the live cases still work.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import random
import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from cfo.data.invoices import SUPPLIERS
from cfo.db.repo import Repository
from cfo.domain import Invoice, InvoiceLine
from scout.audit import Audit
from scout.connectors.ksef import (
    TEST_URL,
    KsefClient,
    Line,
    Party,
    fa3_xml,
    lines_for_gross,
    parse_fa3,
    random_nip,
)
from scout.db import ConnFactory
from scout.demo_data import LIVE_INVOICE_NUMBERS, SCENARIOS

log = logging.getLogger(__name__)

CONNECTION_ID = "ksef"
COMPANY = Party("", "Pizzeria Amalfi Sp. z o.o.", "ul. Neapolitańska 7", "00-001 Warszawa")
KSEF_SUPPLIER = {"name": "Hurtownia Mazur Sp. z o.o.", "street": "ul. Magazynowa 5", "city": "02-235 Warszawa"}
EXTRA_FILED = 5  # already-filed invoices also issued in KSeF, so a sync shows "already on file" too
VAT_PL = {Decimal("0.07"): 8, Decimal("0.19"): 23}
ClientFactory = Callable[[str], KsefClient]

DEFAULT_ROW = {"id": CONNECTION_ID, "kind": "invoicing", "provider": "ksef_test", "display_name": "KSeF · test environment",
               "config": {"env": "test", "api": TEST_URL, "phase": "idle", "steps": []}, "status": "disconnected",
               "enabled": True}


def ensure_row(conn: Any) -> dict[str, Any]:
    conn.execute(
        "INSERT INTO connections (id, kind, provider, display_name, config, status, enabled) "
        "VALUES (%s, %s, %s, %s, %s, %s, TRUE) ON CONFLICT (id) DO NOTHING",
        (CONNECTION_ID, DEFAULT_ROW["kind"], DEFAULT_ROW["provider"], DEFAULT_ROW["display_name"],
         Jsonb(DEFAULT_ROW["config"]), DEFAULT_ROW["status"]))
    return conn.execute("SELECT * FROM connections WHERE id = %s", (CONNECTION_ID,)).fetchone()


def supplier_nip(company_nip: str, key: str) -> str:
    seed = int(hashlib.sha256(f"{company_nip}:{key}".encode()).hexdigest()[:12], 16)
    return random_nip(random.Random(seed))


def _supplier_party(company_nip: str, number: str, counterparty: str) -> Party:
    prefix = number.split("-")[0]
    if prefix in SUPPLIERS:
        s = SUPPLIERS[prefix]
        street, _, city = s["address"].partition(", ")
        return Party(supplier_nip(company_nip, prefix), s["name"], street, city or street)
    return Party(supplier_nip(company_nip, prefix), counterparty or prefix, KSEF_SUPPLIER["street"], KSEF_SUPPLIER["city"])


def invoice_xml(company_nip: str, number: str, issue_date: dt.date, gross: Decimal, counterparty: str,
                lines: list[InvoiceLine] | None = None) -> tuple[Party, bytes]:
    """The supplier's FA(3) for one of our payments; gross equals the payment (lines collapsed to one if needed)."""
    seller = _supplier_party(company_nip, number, counterparty)
    prefix = number.split("-")[0]
    rate = VAT_PL.get(SUPPLIERS.get(prefix, {}).get("vat"), 23)
    default = SUPPLIERS.get(prefix, {}).get("remainder", "Goods as per order")
    desc = lines[0].description if lines and len(lines) == 1 else default
    buyer = Party(company_nip, COMPANY.name, COMPANY.street, COMPANY.city)
    return seller, fa3_xml(number, issue_date, seller, buyer, lines_for_gross(gross, desc, rate),
                           due_date=issue_date + dt.timedelta(days=14))


def new_payables(company_nip: str, today: dt.date, already: set[str]) -> list[tuple[str, dt.date, Decimal, str]]:
    """Three supplier invoices issued this week and not paid yet (TE demo): what a real KSeF sync brings in first."""
    rng = random.Random(f"payables:{company_nip}")
    out = []
    for prefix, days_ago in (("AS", 2), ("FLV", 3), ("CWM", 1)):
        number = f"{prefix}-{rng.randint(80000, 99999)}"
        gross = Decimal(rng.randint(30000, 160000)) / 100
        if number not in already and number not in LIVE_INVOICE_NUMBERS:
            out.append((number, today - dt.timedelta(days=days_ago), gross, SUPPLIERS[prefix]["name"]))
    return out


class Progress:
    """Writes the run's state into `connections.config` (merged) after every step."""

    def __init__(self, conn_factory: ConnFactory, now: Callable[[], dt.datetime]):
        self._cf, self._now = conn_factory, now
        self.steps: list[dict[str, Any]] = []

    def save(self, *, status: str | None = None, error: str | None = None, sync: bool = False, **config: Any) -> None:
        with self._cf() as conn:
            conn.execute(
                "UPDATE connections SET config = config || %s, status = coalesce(%s, status), "
                "last_error = CASE WHEN %s::text IS NULL AND %s THEN NULL ELSE coalesce(%s, last_error) END, "
                "last_sync_at = CASE WHEN %s THEN now() ELSE last_sync_at END WHERE id = %s",
                (Jsonb({**config, "steps": self.steps}), status, error, sync, error, sync, CONNECTION_ID))
            conn.commit()

    def step(self, text: str, ok: bool = True, **config: Any) -> None:
        self.steps.append({"at": self._now().strftime("%Y-%m-%dT%H:%M:%SZ"), "text": text, "ok": ok})
        self.save(**config)


def start(conn: Any) -> bool:
    """Mark a run as started; False when one is already running (the API answers 409)."""
    ensure_row(conn)
    row = conn.execute(
        "UPDATE connections SET config = config || %s WHERE id = %s AND coalesce(config->>'phase', 'idle') <> 'running' "
        "RETURNING id", (Jsonb({"phase": "running", "steps": []}), CONNECTION_ID)).fetchone()
    conn.commit()
    return row is not None


def _candidates(repo: Repository, already: set[str]) -> tuple[list[Any], list[Invoice]]:
    """Payments without an invoice (not the live demo cases) and a few recent filed invoices, not yet issued."""
    open_ = [t for t in repo.list_transactions(undocumented_only=True)
             if t.invoice_number and t.invoice_number not in LIVE_INVOICE_NUMBERS and t.invoice_number not in already
             and t.amount < 0]
    rows = repo.conn.execute(
        "SELECT number FROM invoices WHERE transaction_id IS NOT NULL ORDER BY issue_date DESC LIMIT %s",
        (EXTRA_FILED * 3,)).fetchall()
    filed = []
    for r in rows:
        if r["number"] in already or r["number"] in LIVE_INVOICE_NUMBERS or "-" not in r["number"]:
            continue
        inv = repo.get_invoice_by_number(r["number"])
        if inv:
            filed.append(inv)
        if len(filed) >= EXTRA_FILED:
            break
    return open_, filed


NUMBER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9/._-]{0,63}")
_GENERIC = {"gmbh", "ltd", "limited", "srl", "s.r.l.", "spółka", "spolka", "sp.", "z", "o.o.", "inc", "the", "and", "company"}


def _tokens(name: str | None) -> set[str]:
    return {t for t in re.findall(r"[\w.]+", (name or "").lower()) if len(t) >= 4 and t not in _GENERIC}


def known_sellers(company_nip: str) -> set[str]:
    """NIPs of the suppliers Scout simulates on TE for this company (the only sellers it trusts blindly)."""
    return {supplier_nip(company_nip, p) for p in [*SUPPLIERS, SCENARIOS["ksef"].invoice_number.split("-")[0]]}


def trusted_seller(doc: dict[str, Any], counterparty: str | None, sellers: set[str]) -> bool:
    """KSeF TE is shared: anyone can issue an invoice to our NIP. Link or file one only when its seller is a
    supplier we know, or its name matches the payment's counterparty."""
    return doc.get("seller_nip") in sellers or bool(_tokens(doc.get("supplier")) & _tokens(counterparty))


def invoice_row_id(number: str) -> str:
    return "inv_ksef_" + hashlib.sha256(number.encode()).hexdigest()[:16]


def _import(repo: Repository, doc: dict[str, Any], ksef_number: str, *, our_nip: str,
            sellers: set[str]) -> tuple[str, Decimal]:
    """File one received invoice. Returns (outcome, linked amount):
    linked | on_file | mismatch | imported | review (not ours, odd number, conflicting or untrusted).

    Never overwrites an invoice that is already in the database: a received document with a number we know is at
    most linked to that invoice's open payment when everything matches; new numbers get their own row (id derived
    from a hash of the exact number, so a look-alike number cannot land on another invoice's id)."""
    number, total = doc["number"], doc["total"]
    if not NUMBER_RE.fullmatch(number or "") or doc.get("buyer_nip") != our_nip:
        return "review", Decimal(0)
    with repo.transaction():
        repo.lock_invoice_number(number)
        existing = repo.get_invoice_by_number(number)
        txn = next((t for t in repo.list_transactions(undocumented_only=True) if t.invoice_number == number), None)
        if existing:
            if existing.total != total or existing.supplier != (doc["supplier"] or existing.supplier):
                return "review", Decimal(0)  # same number, different document: a person decides
            if existing.transaction_id:
                return "on_file", Decimal(0)
            if txn is not None and -txn.amount == total and trusted_seller(doc, txn.counterparty, sellers):
                repo.conn.execute("UPDATE invoices SET transaction_id = %s WHERE id = %s", (txn.id, existing.id))
                repo.conn.execute("UPDATE transactions SET documented = TRUE, invoice_id = %s WHERE id = %s",
                                  (existing.id, txn.id))
                return "linked", total
            return "on_file", Decimal(0)
        row_id = invoice_row_id(number)
        if repo.conn.execute("SELECT 1 FROM invoices WHERE id = %s", (row_id,)).fetchone():
            return "review", Decimal(0)
        trusted = txn is not None and trusted_seller(doc, txn.counterparty, sellers)
        link = txn.id if trusted and -txn.amount == total else None
        inv = Invoice(id=row_id, number=number, supplier=(doc["supplier"] or "?")[:200], issue_date=doc["issue_date"],
                      total=total, due_date=doc.get("due_date"), currency=doc["currency"][:3], transaction_id=link,
                      source="ksef", category=txn.category if link else None, team_id=txn.team_id if link else None,
                      lines=[InvoiceLine(description=(ln["description"] or "-")[:300], quantity=ln["quantity"],
                                         unit_price=ln["unit_price"], amount=ln["amount"]) for ln in doc["lines"][:200]])
        if txn is not None and not link:
            return ("mismatch" if trusted else "review"), Decimal(0)
        repo.add_invoice(inv, commit=False)
    return ("linked", total) if link else ("imported", Decimal(0))


def run(conn_factory: ConnFactory, audit: Audit, client_factory: ClientFactory, *,
        base_url: str = TEST_URL, now: Callable[[], dt.datetime] | None = None,
        sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    """The whole one-click flow. Never raises: failures end in phase=error with the reason in the step log."""
    now = now or (lambda: dt.datetime.now(dt.UTC))
    import time as _time
    sleep = sleep or _time.sleep
    with conn_factory() as conn:
        row = ensure_row(conn)
        conn.commit()
    cfg = dict(row["config"] or {})
    p = Progress(conn_factory, now)
    try:
        client = client_factory(base_url)
        nip = cfg.get("nip")
        if not nip:
            nip = random_nip()
            p.step(f"Registered the company in KSeF TE under the test NIP {nip}", nip=nip, company=COMPANY.name,
                   env="test", api=base_url, phase="running", connected_at=now().isoformat())
        token = client.login(nip, COMPANY.name)
        p.step(f"Signed in to KSeF as NIP {nip} (XAdES signature, self-signed company seal, test only)", phase="running")
        connected_at = dt.datetime.fromisoformat(cfg.get("connected_at") or now().isoformat())

        issued_numbers = set(cfg.get("issued_numbers") or [])
        conn = conn_factory()
        try:
            open_, filed = _candidates(Repository(conn), issued_numbers)
        finally:
            conn.close()
        batches: dict[str, tuple[Party, list[tuple[str, bytes]]]] = {}
        for t in open_:
            seller, xml = invoice_xml(nip, t.invoice_number, t.booking_date, -t.amount, t.counterparty)
            batches.setdefault(seller.nip, (seller, []))[1].append((t.invoice_number, xml))
        for inv in filed:
            seller, xml = invoice_xml(nip, inv.number, inv.issue_date, inv.total, inv.supplier, inv.lines)
            batches.setdefault(seller.nip, (seller, []))[1].append((inv.number, xml))
        for number, issue_date, gross, supplier in new_payables(nip, now().date(), issued_numbers):
            seller, xml = invoice_xml(nip, number, issue_date, gross, supplier)
            batches.setdefault(seller.nip, (seller, []))[1].append((number, xml))
        issued = int(cfg.get("issued") or 0)
        for seller, items in batches.values():
            stoken = client.login(seller.nip, seller.name)
            results = client.send(stoken, [x for _, x in items])
            ok = [n for (n, _), r in zip(items, results) if r.get("ok")]
            issued += len(ok)
            issued_numbers |= set(ok)
            failed = [f"{n}: {r.get('error')}" for (n, _), r in zip(items, results) if not r.get("ok")]
            p.step(f"{seller.name} (NIP {seller.nip}) issued {len(ok)} e-invoice{'s' if len(ok) != 1 else ''} to us "
                   f"in KSeF{' (' + '; '.join(failed)[:200] + ')' if failed else ''} · simulated supplier",
                   ok=not failed, issued=issued, issued_numbers=sorted(issued_numbers))
        if not batches:
            p.step("No new supplier invoices to issue (TE already has them)")

        metas: list[dict[str, Any]] = []
        since = connected_at - dt.timedelta(hours=1)
        for _ in range(15):
            metas = client.received(token, since)
            if len({m.get("invoiceNumber") for m in metas} & issued_numbers) >= len(issued_numbers):
                break
            sleep(2.0)
        p.step(f"Queried KSeF for invoices addressed to NIP {nip}: {len(metas)} found", received=len(metas))

        counts = {"linked": 0, "on_file": 0, "mismatch": 0, "imported": 0, "review": 0}
        linked_total = unpaid_total = Decimal(0)
        listing = []
        conn = conn_factory()
        try:
            repo = Repository(conn)
            for m in metas:
                doc = parse_fa3(client.download(token, m["ksefNumber"]))
                if not doc["number"]:
                    continue
                outcome, amount = _import(repo, doc, m["ksefNumber"], our_nip=nip, sellers=known_sellers(nip))
                counts[outcome] += 1
                linked_total += amount
                unpaid_total += doc["total"] if outcome == "imported" else Decimal(0)
                listing.append({"number": doc["number"], "ksef_number": m["ksefNumber"], "supplier": doc["supplier"],
                                "total": str(doc["total"]), "currency": doc["currency"],
                                "linked": outcome in ("linked", "on_file"),
                                "outcome": outcome})
        finally:
            conn.close()
        listing.sort(key=lambda r: (not r["linked"], r["number"]))
        parts = [f"{counts['on_file']} already on file"]
        if counts["linked"]:
            parts.insert(0, f"{counts['linked']} matched to payments that had no invoice (€{linked_total:,.2f})")
        text = f"Downloaded {len(listing)} FA(3) invoices: " + ", ".join(parts)
        if counts["imported"]:
            text += f", {counts['imported']} new and not paid yet (€{unpaid_total:,.2f}, added to payables)"
        if counts["mismatch"]:
            text += f", {counts['mismatch']} with a different amount than the payment"
        if counts["review"]:
            text += f", {counts['review']} left for review (unknown seller, conflicting or not addressed to us)"
        p.step(text, imported=len(listing), linked=counts["linked"], linked_total=str(linked_total),
               on_file=counts["on_file"], unpaid=counts["imported"], unpaid_total=str(unpaid_total),
               invoices=listing[:30])
        p.step("KSeF connected. Scout now checks KSeF when a payment has no invoice.", phase="done")
        p.save(status="connected", sync=True, phase="done")
        audit.record("dashboard", "connect", "invoicing:ksef",
                     f"KSeF TE NIP {nip}: {len(listing)} received, {counts['linked']} matched")
        return {"nip": nip, "received": len(metas), **counts, "linked_total": str(linked_total)}
    except Exception as e:  # noqa: BLE001 - the dashboard shows the reason; the job must not retry forever
        log.exception("KSeF connect failed")
        p.step(f"Failed: {str(e)[:300]}", ok=False)
        p.save(status="error", error=str(e)[:500], phase="error")
        return {"error": str(e)}


def demo_nip(conn: Any) -> str | None:
    row = conn.execute("SELECT config FROM connections WHERE id = %s", (CONNECTION_ID,)).fetchone()
    return ((row or {}).get("config") or {}).get("nip") if row else None


def issue_demo_invoice(nip: str | None, client_factory: ClientFactory, *, base_url: str = TEST_URL) -> str:
    """Demo case `ksef`: the supplier issues the invoice in KSeF right now (Scout has not synced it yet).
    Returns the KSeF number. `nip` is ours (from the connected `ksef` row); None -> LookupError."""
    if not nip:
        raise LookupError("Connect KSeF first")
    s = SCENARIOS["ksef"]
    client = client_factory(base_url)
    seller = Party(supplier_nip(nip, s.invoice_number.split("-")[0]), s.counterparty, KSEF_SUPPLIER["street"],
                   KSEF_SUPPLIER["city"])
    buyer = Party(nip, COMPANY.name, COMPANY.street, COMPANY.city)
    today = dt.date.today()
    lines = [Line("Mąka typ 00 Caputo, worek 25 kg", Decimal(20), Decimal("24.00"), 8),
             Line("Pomidory San Marzano DOP, puszka 2,5 kg", Decimal(60), Decimal("12.50"), 8),
             Line("Oliwa extra vergine, kanister 5 l", Decimal(6), Decimal("28.00"), 8)]
    xml = fa3_xml(s.invoice_number, today, seller, buyer, lines, due_date=today + dt.timedelta(days=7))
    total = parse_fa3(xml)["total"]
    if total != -s.amount:
        raise ValueError(f"demo invoice total {total} != payment {-s.amount}")
    res = client.send(client.login(seller.nip, seller.name), [xml])[0]
    if not res.get("ok"):
        raise RuntimeError(f"KSeF rejected the demo invoice: {res.get('error')}")
    return res["ksefNumber"]
