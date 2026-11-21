"""Transparent growth scenarios and public-news monitoring; no LLM or external company-data upload."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from statistics import median
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

FEEDS = {
    "ECB": "https://www.ecb.europa.eu/rss/press.html",
    "Eurostat": "https://ec.europa.eu/eurostat/en/search?_estatsearchportlet_WAR_estatsearchportlet_collection=CAT_EURNEW&p_p_id=estatsearchportlet_WAR_estatsearchportlet&p_p_lifecycle=2&p_p_mode=view&p_p_resource_id=atom&p_p_state=maximized",
}
MAX_BYTES = 1_000_000
# Each relevance reason is a hypothesis to inspect, never a claimed financial effect.
EXPOSURES = {
    "food": (("food", "agricultur", "producer price"), "Food purchasing", "Compare the next supplier quote with recent invoices before changing menu prices."),
    "beverages": (("food", "beverage", "alcohol"), "Beverage purchasing", "Check supplier pricing and product margins."),
    "utilities": (("energy", "electric", "gas price"), "Energy bills", "Check contract renewal dates and compare current utility bills."),
    "payroll": (("wage", "labour cost", "labor cost", "minimum wage"), "Staffing costs", "Review staffing capacity and payroll assumptions before hiring."),
    "rent": (("rent", "property", "inflation"), "Rent exposure", "Check whether the lease has an indexation clause and when it resets."),
    "partner_investment": (("exchange rate", "swiss", "franc"), "Cross-border investment", "Confirm the invoice currency and test an exchange-rate scenario before committing."),
    "revenue": (("consumer", "retail", "tourism", "household consumption"), "Customer demand", "Compare local order volume and average ticket with this broader demand signal."),
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text or "")).strip()[:500]


def parse_feed(data: bytes, source: str, now: datetime, categories: set[str]) -> list[dict]:
    if len(data) > MAX_BYTES or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("Oversized or unsafe feed")
    root = ET.fromstring(data)
    if root.tag.split("}")[-1] not in {"rss", "feed", "RDF"}:
        raise ValueError("Not a news feed")
    out, seen = [], set()
    for item in list(root.iter()):
        if item.tag.split("}")[-1] not in {"item", "entry"}:
            continue
        fields = {child.tag.split("}")[-1]: child for child in item}
        def value(key):
            node = fields.get(key)
            return "" if node is None else "".join(node.itertext())
        title = clean(value("title"))
        link = fields.get("link")
        url = ((link.get("href") or link.text or "") if link is not None else "").strip()
        if urlparse(url).scheme != "https" or not urlparse(url).hostname or url in seen:
            continue
        raw_date = value("pubDate") or value("published") or value("updated") or value("date")
        try:
            published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            try:
                published = parsedate_to_datetime(raw_date)
            except (ValueError, TypeError, IndexError):
                continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if not now - timedelta(days=14) <= published <= now:
            continue
        text = (title + " " + clean(value("description") or value("summary"))).casefold()
        matches = [(label, action) for cat, (words, label, action) in EXPOSURES.items()
                   if cat in categories and any(word in text for word in words)]
        if not matches:
            continue
        seen.add(url)
        out.append({"title": title, "url": url, "published_at": published.isoformat(), "source": source,
                    "relevance": "; ".join(label for label, _ in matches) + " appears in the company ledger; possible relevance, not a proven impact.",
                    "action": matches[0][1]})
    return sorted(out, key=lambda item: item["published_at"], reverse=True)[:5]


def fetch_news(now: datetime, categories: set[str], client=None) -> dict:
    sources, items = [], []
    owned = client is None
    client = client or httpx.Client(timeout=8, follow_redirects=False, headers={"User-Agent": "Scout-CFO/1.0 (public news monitor)"})
    try:
        for name, url in FEEDS.items():
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ValueError("Feed exceeds size limit")
                found = parse_feed(bytes(data), name, now, categories)
                items.extend(found)
                sources.append({"name": name, "url": url, "status": "ok", "relevant_count": len(found)})
            except (httpx.HTTPError, ValueError, ET.ParseError) as exc:
                sources.append({"name": name, "url": url, "status": "unavailable", "error": type(exc).__name__})
    finally:
        if owned:
            client.close()
    ok = sum(s["status"] == "ok" for s in sources)
    unique = {i["url"]: i for i in items}
    return {"status": "ok" if ok == len(sources) else "partial" if ok else "unavailable",
            "sources": sources, "items": sorted(unique.values(), key=lambda i: i["published_at"], reverse=True)[:5]}


def month_after(day: date, offset: int = 1) -> date:
    n = day.year * 12 + day.month - 1 + offset
    return date(n // 12, n % 12 + 1, 1)


def growth_scenarios(rows: list[dict], as_of: date, currency: str = "EUR") -> dict:
    """Bank-collected revenue proxy, excluding current/first observed months and non-revenue inflows."""
    relevant = [r for r in rows if r["booking_date"] <= as_of and r["currency"] == currency]
    result = {"status": "insufficient_data", "currency": currency, "monthly_revenue": [], "scenarios": [],
              "observed_growth_pct": None, "latest_month": None, "limitations": [
                  "Bank-collected revenue is a sales proxy, not accrual revenue or profit.",
                  "First observed and current months excluded because they may be partial imports or incomplete periods.",
                  "Calendar-complete months can still have missing bookings; reconcile before making commitments.",
                  "Scenarios are assumptions, not a validated growth prediction; capacity and seasonality are not modelled."]}
    if any(r["currency"] != currency for r in rows):
        result["limitations"].append(f"Other currencies excluded; no FX conversion into {currency} was assumed.")
    if not relevant:
        return result
    first = min(r["booking_date"] for r in relevant).replace(day=1)
    cutoff = as_of.replace(day=1)
    # The first observed month may be an incomplete import; always exclude it.
    monthly = {}
    for r in relevant:
        month = r["booking_date"].replace(day=1)
        if first < month < cutoff and r["category"] == "revenue":
            monthly[month] = monthly.get(month, Decimal(0)) + Decimal(str(r["amount"]))
    dates = sorted(monthly)[-6:]
    result["monthly_revenue"] = [{"month": d.isoformat(), "revenue": str(monthly[d].quantize(Decimal('.01')))} for d in dates]
    if not dates:
        return result
    last = dates[-1]
    result["latest_month"] = last.isoformat()
    if last != month_after(cutoff, -1):
        result["status"] = "stale_data"
        result["limitations"].append("Latest completed revenue month is older than last month. Refresh the ledger before forecasting.")
        return result
    if len(dates) < 3 or any(month_after(a) != b for a, b in zip(dates, dates[1:])) or any(monthly[d] <= 0 for d in dates):
        result["limitations"].append("Need at least three consecutive completed months of positive collected revenue.")
        return result
    changes = [monthly[b] / monthly[a] - 1 for a, b in zip(dates, dates[1:])]
    trend = median(changes)
    result["observed_growth_pct"] = str((trend * 100).quantize(Decimal('.01')))
    bounded = max(Decimal('-.2'), min(Decimal('.2'), trend))
    if bounded != trend:
        result["limitations"].append("Observed monthly growth capped at ±20% for extrapolation.")
    spread = max(Decimal('.02'), max(abs(x - trend) for x in changes))
    spread = min(Decimal('.10'), spread)
    for name, rate in [("downside", max(Decimal('-.3'), bounded-spread)), ("base", bounded), ("upside", min(Decimal('.3'), bounded+spread))]:
        result["scenarios"].append({"name": name, "monthly_growth_pct": str((rate*100).quantize(Decimal('.01'))),
            "months": [{"month": month_after(last, n).isoformat(), "revenue": str((monthly[last]*(1+rate)**n).quantize(Decimal('.01')))} for n in range(1, 4)]})
    result["status"] = "scenario"
    return result
