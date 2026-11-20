"""Monthly business metrics around the bank statement: orders, ads and new customers, cloud invoices.

Three cost/metric pairs (D-006): food cost vs orders, ad spend vs new customers, app cloud spend vs online
orders. December 2025 carries the anomaly of the demo: the cloud invoice is +50% m/m while online orders
grow only +3% (the log-ingestion line explodes). Ads, cloud and equipment are paid by company credit card,
not from the bank account, so the organizer's statement stays untouched.
"""

from __future__ import annotations

import random
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from cfo.domain import CostMetric, CostMetricKind, Invoice, InvoiceLine, Transaction

CENT = Decimal("0.01")
CLOUD_SUPPLIER = "Nimbus Cloud EU"
ADS_SUPPLIER = "Meridian Ads (social and search)"
SPIKE_MONTH = 12
SPIKE_COST_FACTOR = Decimal("1.5")
SPIKE_ORDERS_FACTOR = Decimal("1.03")

# service, FOCUS ServiceCategory, unit, unit price EUR
CLOUD_SERVICES = [
    ("Compute (app backend)", "Compute", "vCPU-hour", Decimal("0.0420")),
    ("Managed database", "Databases", "instance-hour", Decimal("0.0900")),
    ("Object storage (menu photos)", "Storage", "GB-month", Decimal("0.0230")),
    ("Data transfer out", "Networking", "GB", Decimal("0.0800")),
    ("Log ingestion and retention", "Management and Governance", "GB", Decimal("0.5000")),
    ("API gateway", "Networking", "million requests", Decimal("3.2000")),
]


def _q(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def month_start(m: int, year: int = 2025) -> date:
    return date(year, m, 1)


@dataclass
class MonthData:
    month: date
    food_cost: Decimal
    orders: int
    online_orders: int
    ad_spend: Decimal
    new_customers: int
    cloud_invoice: Invoice
    ads_invoice: Invoice
    focus_rows: list[dict]
    cost_metrics: list[CostMetric] = field(default_factory=list)


def _rng(seed: int, name: str) -> random.Random:
    return random.Random(f"{seed}:{name}")


def _cloud_lines(online: int, month_idx: int) -> list[tuple[str, str, str, Decimal, Decimal]]:
    qty = {
        "Compute (app backend)": Decimal(2200 + 3 * online),
        "Managed database": Decimal(720),
        "Object storage (menu photos)": Decimal(300 + 20 * month_idx),
        "Data transfer out": Decimal(40) + Decimal(online) * Decimal("0.35"),
        "Log ingestion and retention": Decimal(40) + Decimal(online) * Decimal("0.05"),
        "API gateway": Decimal("0.4") + Decimal(online) * Decimal("0.0007"),
    }
    return [(s, c, u, _q(qty[s]), p) for s, c, u, p in CLOUD_SERVICES]


def build_months(txns: list[Transaction], seed: int) -> list[MonthData]:
    revenue: dict[int, Decimal] = {m: Decimal(0) for m in range(1, 13)}
    food: dict[int, dict[str, Decimal]] = {m: {} for m in range(1, 13)}
    for t in txns:
        m = t.booking_date.month
        if t.category == "revenue":
            revenue[m] += t.amount
        elif t.category in ("food", "beverages"):
            food[m][t.counterparty] = food[m].get(t.counterparty, Decimal(0)) - t.amount
    rng = _rng(seed, "metrics")
    months: list[MonthData] = []
    online_prev = 0
    cloud_total_prev = Decimal(0)
    for m in range(1, 13):
        start = month_start(m)
        end = date(2025, m, monthrange(2025, m)[1])
        orders = round(float(revenue[m]) / rng.uniform(21.0, 25.0))
        if m == SPIKE_MONTH:
            online = round(online_prev * float(SPIKE_ORDERS_FACTOR))
        else:
            online = round(orders * (0.06 + 0.007 * (m - 1) + rng.uniform(-0.004, 0.004)))
        lines = _cloud_lines(online, m)
        if m == SPIKE_MONTH:
            others = sum(_q(q * p) for s, _, _, q, p in lines if not s.startswith("Log"))
            target = _q(cloud_total_prev * SPIKE_COST_FACTOR) - others
            log_price = CLOUD_SERVICES[4][3]
            lines = [(s, c, u, _q(target / log_price) if s.startswith("Log") else q, p) for s, c, u, q, p in lines]
        focus, inv_lines = [], []
        for s, c, u, q, p in lines:
            cost = _q(q * p)
            inv_lines.append(InvoiceLine(description=f"{s} ({u})", quantity=q, unit_price=p, amount=cost, category=c))
            focus.append({
                "ChargePeriodStart": start.isoformat(), "ChargePeriodEnd": end.isoformat(),
                "ServiceName": s, "ServiceCategory": c, "ChargeCategory": "Usage",
                "ConsumedQuantity": str(q), "ConsumedUnit": u, "ContractedUnitPrice": str(p),
                "BilledCost": str(cost), "BillingCurrency": "EUR", "SubAccountName": "pizzeria-order-app",
            })
        cloud_total = sum((ln.amount for ln in inv_lines), Decimal(0))
        cloud_inv = Invoice(
            id=f"inv_cld2025{m:02d}", number=f"CLD-2025-{m:02d}", supplier=CLOUD_SUPPLIER, issue_date=end,
            due_date=date(end.year + (end.month == 12), end.month % 12 + 1, 15), total=cloud_total, lines=inv_lines,
            category="cloud", team_id="it", source="db",
        )
        ad_spend = _q(Decimal(rng.uniform(700, 900)) * (Decimal("0.3") if m == 6 else Decimal(1)))
        cac = rng.uniform(11.0, 15.0)
        new_customers = round(float(ad_spend) / cac / (4 if m == 6 else 1))  # June: closed, teaser campaign only
        meta = _q(ad_spend * Decimal("0.6"))
        ads_inv = Invoice(
            id=f"inv_ads2025{m:02d}", number=f"ADS-2025-{m:02d}", supplier=ADS_SUPPLIER, issue_date=end,
            due_date=None, total=ad_spend, category="advertising", team_id="marketing",
            lines=[InvoiceLine(description="Social ads (Instagram, Facebook)", unit_price=meta, amount=meta,
                               category="advertising"),
                   InvoiceLine(description="Search ads (local pizza delivery)", unit_price=ad_spend - meta,
                               amount=ad_spend - meta, category="advertising")],
        )
        food_cost = sum(food[m].values(), Decimal(0))
        data = MonthData(start, food_cost, orders, online, ad_spend, new_customers, cloud_inv, ads_inv, focus)
        data.cost_metrics = [
            CostMetric(id=f"cm_food_{start:%Y%m}", kind=CostMetricKind.FOOD_COST, month=start, team_id="kitchen",
                       cost=food_cost, metric_value=Decimal(orders),
                       detail={"by_supplier": {k: str(v) for k, v in sorted(food[m].items())}}),
            CostMetric(id=f"cm_ads_{start:%Y%m}", kind=CostMetricKind.ADS_CAC, month=start, team_id="marketing",
                       cost=ad_spend, metric_value=Decimal(new_customers),
                       detail={"invoice_number": ads_inv.number}),
            CostMetric(id=f"cm_cloud_{start:%Y%m}", kind=CostMetricKind.CLOUD_PER_ORDER, month=start, team_id="it",
                       cost=cloud_total, metric_value=Decimal(online),
                       detail={"invoice_number": cloud_inv.number,
                               "lines": [{"service": s, "category": c, "unit": u, "quantity": str(q),
                                          "cost": str(_q(q * p))} for s, c, u, q, p in lines]}),
        ]
        months.append(data)
        online_prev, cloud_total_prev = online, cloud_total
    return months


def equipment_invoice() -> Invoice:
    """The one equipment purchase of the year (October): a second pizza oven deck, paid by company card."""
    unit = Decimal("2890.00")
    delivery = Decimal("120.00")
    vat = _q((unit + delivery) * Decimal("0.19"))
    return Invoice(
        id="inv_eq20251003", number="EQ-2025-1003", supplier="Gastro Equip Italia S.r.l.", issue_date=date(2025, 10, 3),
        due_date=date(2025, 10, 17), total=unit + delivery + vat, category="equipment", team_id="equipment",
        lines=[InvoiceLine(description="Two-chamber electric pizza oven deck", unit_price=unit, amount=unit,
                           category="equipment"),
               InvoiceLine(description="Delivery and installation", unit_price=delivery, amount=delivery,
                           category="equipment"),
               InvoiceLine(description="VAT 19%", unit_price=vat, amount=vat, category="vat")],
    )
