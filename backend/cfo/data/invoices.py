"""Supplier invoices (AS-/FLV-/CWM-/PFB-) matching bank payments to the cent, as model, PDF and .eml."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from email.message import EmailMessage
from email.utils import format_datetime

from cfo.data.pdf import TextItem, render_pdf
from cfo.domain import Invoice, InvoiceLine, Transaction

CENT = Decimal("0.01")

SUPPLIERS = {
    "AS": {
        "name": "Asimex Wholesale Ltd",
        "address": "Hafenstrasse 41, 60314 Frankfurt am Main",
        "email": "billing@asimex-wholesale.example",
        "vat": Decimal("0.07"),
        "category": "food",
        "catalog": [
            ("San Marzano tomatoes, 2.5 kg tin", "4.90"), ("Mozzarella di bufala, 1 kg", "11.80"),
            ("Flour tipo 00, 25 kg sack", "21.50"), ("Extra virgin olive oil, 5 l", "38.00"),
            ("Parmigiano Reggiano 24m, 1 kg", "24.20"), ("Prosciutto crudo, 1 kg", "27.40"),
            ("Fresh basil, bunch", "1.60"), ("Gorgonzola DOP, 1 kg", "13.90"),
            ("Dried oregano, 500 g", "6.30"), ("Pizza boxes 32 cm, 100 pcs", "17.50"),
            ("Salsiccia piccante, 1 kg", "12.60"), ("Anchovies in oil, 700 g", "9.40"),
        ],
        "remainder": "Assorted specialty ingredients",
    },
    "FLV": {
        "name": "Fattoria La Vino GmbH",
        "address": "Weinbergweg 8, 65183 Wiesbaden",
        "email": "rechnung@fattoria-la-vino.example",
        "vat": Decimal("0.19"),
        "category": "beverages",
        "catalog": [
            ("Montepulciano d'Abruzzo 0.75 l, case of 6", "42.00"), ("Prosecco DOC 0.75 l, case of 6", "36.00"),
            ("Peroni 0.33 l, crate of 24", "28.00"), ("San Pellegrino 0.75 l, case of 12", "14.50"),
            ("Chianti Classico 0.75 l, case of 6", "58.00"), ("Limoncello 0.7 l", "16.90"),
            ("Aperol 1 l", "15.80"), ("Pinot Grigio 0.75 l, case of 6", "39.00"),
        ],
        "remainder": "Assorted beverages and deposits",
    },
    "CWM": {
        "name": "Central Wholesale Market",
        "address": "Grossmarkthalle 3, 60314 Frankfurt am Main",
        "email": "invoices@central-wholesale.example",
        "vat": Decimal("0.07"),
        "category": "food",
        "catalog": [
            ("Tomatoes, crate 10 kg", "18.40"), ("Rocket salad, 1 kg", "9.80"), ("Mushrooms champignons, 3 kg", "14.10"),
            ("Red onions, 10 kg sack", "11.20"), ("Zucchini, 5 kg", "10.90"), ("Lemons, 5 kg", "12.30"),
            ("Bell peppers mixed, 5 kg", "16.70"), ("Garlic, 1 kg", "7.40"),
        ],
        "remainder": "Assorted fresh produce",
    },
    "PFB": {
        "name": "Pasta Fresca Bologna S.r.l.",
        "address": "Via Indipendenza 22, 40121 Bologna",
        "email": "amministrazione@pastafresca-bologna.example",
        "vat": Decimal("0.07"),
        "category": "food",
        "catalog": [
            ("Tagliatelle fresche, 5 kg", "34.00"), ("Tortellini ricotta e spinaci, 3 kg", "41.50"),
            ("Ravioli al tartufo, 2 kg", "58.00"), ("Gnocchi di patate, 5 kg", "22.80"),
        ],
        "remainder": "Assorted fresh pasta",
    },
}
CUSTOMER = ["Pizzeria Amalfi", "Vincenzo Amalfi", "Main Street 12", "60311 Frankfurt am Main"]


def _q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def supplier_of(number: str) -> dict:
    return SUPPLIERS[number.split("-")[0]]


def invoice_id(number: str) -> str:
    return "inv_" + number.replace("-", "").lower()


def build_invoice(txn: Transaction, rng: random.Random, *, transaction_id: str | None) -> Invoice:
    """An invoice whose lines (net items + VAT) sum exactly to the payment."""
    sup = supplier_of(txn.invoice_number)
    gross = -txn.amount
    net = _q(gross / (1 + sup["vat"]))
    vat = gross - net
    lines: list[InvoiceLine] = []
    spent = Decimal(0)
    for desc, price in rng.sample(sup["catalog"], rng.randint(3, 5)):
        unit = _q(Decimal(price) * Decimal(str(rng.uniform(0.96, 1.05))))
        budget = net * Decimal(str(rng.uniform(0.12, 0.28)))
        qty = max(1, int(budget / unit))
        amount = _q(unit * qty)
        if spent + amount > net * Decimal("0.9"):
            break
        lines.append(InvoiceLine(description=desc, quantity=Decimal(qty), unit_price=unit, amount=amount,
                                 category=sup["category"]))
        spent += amount
    rest = net - spent
    lines.append(InvoiceLine(description=sup["remainder"], quantity=Decimal(1), unit_price=rest, amount=rest,
                             category=sup["category"]))
    lines.append(InvoiceLine(description=f"VAT {int(sup['vat'] * 100)}%", quantity=Decimal(1), unit_price=vat,
                             amount=vat, category="vat"))
    assert sum(ln.amount for ln in lines) == gross
    issue = txn.booking_date - timedelta(days=rng.randint(3, 9))
    return Invoice(
        id=invoice_id(txn.invoice_number), number=txn.invoice_number, supplier=sup["name"], issue_date=issue,
        due_date=issue + timedelta(days=14), total=gross, lines=lines, transaction_id=transaction_id,
        category=sup["category"], team_id="kitchen",
    )


def invoice_pdf(inv: Invoice, address: str = "", title_prefix: str = "INVOICE", notes: tuple[str, ...] = ()) -> bytes:
    items: list[TextItem] = [
        (50, 790, 16, True, inv.supplier),
        (50, 774, 9, False, address),
        (50, 740, 14, True, f"{title_prefix} {inv.number}"),
        (50, 722, 10, False, f"Invoice date: {inv.issue_date.strftime('%d.%m.%Y')}"),
        (50, 708, 10, False, f"Due date: {inv.due_date.strftime('%d.%m.%Y') if inv.due_date else '-'}"),
    ]
    for i, text in enumerate(CUSTOMER):
        items.append((380, 722 - 13 * i, 10, i == 0, text))
    y = 640
    items += [(50, y, 9, True, "Description"), (330, y, 9, True, "Qty"), (385, y, 9, True, "Unit price"),
              (480, y, 9, True, "Amount (EUR)")]
    y -= 16
    for ln in inv.lines:
        items += [(50, y, 9, False, ln.description), (330, y, 9, False, f"{ln.quantity.normalize():f}"),
                  (385, y, 9, False, f"{ln.unit_price:.2f}"), (480, y, 9, False, f"{ln.amount:.2f}")]
        y -= 14
    y -= 10
    items += [(385, y, 11, True, "Total (EUR)"), (480, y, 11, True, f"{inv.total:.2f}")]
    items += [(50, 80, 8, False, f"Payment: bank transfer, reference {inv.number}. Thank you for your business."),
              (50, 68, 8, False, f"{inv.supplier} - {address}")]
    items += [(50, 120 - 12 * i, 8, False, line) for i, line in enumerate(notes)]  # free text under the totals
    return render_pdf(items, title=f"{title_prefix} {inv.number}")


def invoice_text(inv: Invoice) -> str:
    rows = [f"Invoice {inv.number} from {inv.supplier}", f"Date: {inv.issue_date.isoformat()}", ""]
    rows += [f"- {ln.description}: {ln.quantity.normalize():f} x {ln.unit_price:.2f} = {ln.amount:.2f} EUR"
             for ln in inv.lines]
    rows += ["", f"Total: {inv.total:.2f} EUR"]
    return "\n".join(rows)


def invoice_eml(inv: Invoice, pdf: bytes, seq: int) -> bytes:
    sup = supplier_of(inv.number)
    msg = EmailMessage()
    msg["From"] = f"{inv.supplier} <{sup['email']}>"
    msg["To"] = "Grazyna Nowak <grazyna@pizzeria-amalfi.example>"
    msg["Subject"] = f"Invoice {inv.number} - Pizzeria Amalfi"
    msg["Date"] = format_datetime(datetime.combine(inv.issue_date, time(8, 15), tzinfo=timezone.utc))
    msg["Message-ID"] = f"<house-{seq:04d}@{sup['email'].split('@')[1]}>"
    msg.set_content(f"Dear Pizzeria Amalfi team,\n\nplease find attached invoice {inv.number}.\n\n"
                    f"{invoice_text(inv)}\n\nKind regards\n{inv.supplier}\n")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"{inv.number}.pdf")
    msg.set_boundary(f"==house-boundary-{seq:04d}==")
    return msg.as_bytes()
