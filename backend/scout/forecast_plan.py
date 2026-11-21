"""The CEO's annual revenue expectation, Scout's realistic 12-month forecast, and the weak points between them.

Pure helpers (no I/O) plus the small `forecast_plan` store. Money is Decimal, revenue is bank-collected
(`transactions.category = 'revenue'`), a proxy for sales, not accrual revenue.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import Any

DEFAULT_UPLIFT = Decimal("1.5")  # default expectation: 50% above what the company collected in the last 12 months
CENT = Decimal("0.01")
MAX_TREND = Decimal("0.05")  # a realistic forecast never extrapolates more than ±5% a month


def money(v: Any) -> Decimal:
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def month_start(d: date, offset: int = 0) -> date:
    n = d.year * 12 + d.month - 1 + offset
    return date(n // 12, n % 12 + 1, 1)


@dataclass(frozen=True)
class Plan:
    annual_revenue_target: Decimal
    no_hiring: bool = False
    max_new_monthly_cost: Decimal | None = None
    guidance: str = ""
    enabled: bool = True

    def as_json(self) -> dict[str, Any]:
        return {"annual_revenue_target": str(money(self.annual_revenue_target)), "no_hiring": self.no_hiring,
                "max_new_monthly_cost": None if self.max_new_monthly_cost is None else str(money(self.max_new_monthly_cost)),
                "guidance": self.guidance, "enabled": self.enabled}


# -- history -----------------------------------------------------------------------------------------------------
def monthly_revenue(rows: list[dict[str, Any]], as_of: date) -> dict[date, Decimal]:
    """Collected revenue per completed month for the 12 months before `as_of`'s month (missing months = 0)."""
    first, current = month_start(as_of, -12), month_start(as_of)
    out = {month_start(first, i): Decimal(0) for i in range(12)}
    for r in rows:
        m = month_start(r["booking_date"])
        if first <= m < current and r.get("category") == "revenue" and Decimal(str(r["amount"])) > 0:
            out[m] += Decimal(str(r["amount"]))
    return out


def trailing_revenue(rows: list[dict[str, Any]], as_of: date) -> Decimal:
    return money(sum(monthly_revenue(rows, as_of).values(), Decimal(0)))


def default_target(trailing: Decimal) -> Decimal:
    return money(trailing * DEFAULT_UPLIFT)


# -- forecast ----------------------------------------------------------------------------------------------------
def trend(history: list[Decimal]) -> Decimal:
    """Median month-on-month growth of the non-zero months, capped at ±5%."""
    pairs = [(a, b) for a, b in zip(history, history[1:]) if a > 0 and b > 0]
    if not pairs:
        return Decimal(0)
    g = median([b / a - 1 for a, b in pairs])
    return max(-MAX_TREND, min(MAX_TREND, g))


def run_rate(history: list[Decimal]) -> Decimal:
    recent = [v for v in history[-3:] if v > 0]
    return sum(recent, Decimal(0)) / len(recent) if recent else Decimal(0)


def project(history: list[Decimal], months: int = 12) -> list[Decimal]:
    """Scout's realistic forecast: the recent run rate grown at the observed (capped) trend."""
    base, g = run_rate(history), trend(history)
    return [money(base * (1 + g) ** (i + 1)) for i in range(months)]


def target_path(history: list[Decimal], target: Decimal, months: int = 12) -> list[Decimal]:
    """A straight ramp from today's run rate whose 12 months add up to the annual target."""
    r0 = run_rate(history)
    if target <= 12 * r0:  # already at or above the expectation: hold an even monthly target
        flat = [money(target / months)] * months
        flat[-1] = money(flat[-1] + target - sum(flat, Decimal(0)))
        return flat
    # sum_{i=1..12} (r0 + (rN - r0) * i/12) = 12*r0 + 6.5*(rN - r0) = target
    r_n = r0 + (target - 12 * r0) / Decimal("6.5")
    path = [r0 + (r_n - r0) * Decimal(i + 1) / months for i in range(months)]
    out = [money(max(Decimal(0), v)) for v in path]
    out[-1] = money(out[-1] + target - sum(out, Decimal(0)))  # rounding lands on the last month
    return out


# -- weak points -------------------------------------------------------------------------------------------------
def cost_growth(rows: list[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    """Per cost category: last 6 months vs the 6 before, compared with revenue growth over the same windows."""
    cur, start, mid = month_start(as_of), month_start(as_of, -12), month_start(as_of, -6)
    recent: dict[str, dict[str, Any]] = {}
    rev = {"old": Decimal(0), "new": Decimal(0)}
    for r in rows:
        m, amount = month_start(r["booking_date"]), Decimal(str(r["amount"]))
        if not start <= m < cur:
            continue
        half = "new" if m >= mid else "old"
        if r.get("category") == "revenue" and amount > 0:
            rev[half] += amount
        elif amount < 0 and r.get("category") not in (None, "", "revenue", "tax", "transfer", "financing"):
            c = recent.setdefault(r["category"], {"old": Decimal(0), "new": Decimal(0), "team_id": r.get("team_id")})
            c[half] += -amount
            c["team_id"] = c["team_id"] or r.get("team_id")
    rev_growth = rev["new"] / rev["old"] - 1 if rev["old"] > 0 else Decimal(0)
    out = []
    for cat, c in recent.items():
        if c["old"] <= 0:
            continue
        growth = c["new"] / c["old"] - 1
        excess = c["new"] - c["old"] * (1 + rev_growth)  # spend above what revenue growth would justify (6 months)
        if growth > rev_growth and excess > 0:
            out.append({"category": cat, "team_id": c["team_id"], "growth": growth, "revenue_growth": rev_growth,
                        "impact_annual": money(excess * 2)})
    return sorted(out, key=lambda x: x["impact_annual"], reverse=True)


def pct(v: Decimal) -> str:
    return f"{'+' if v >= 0 else ''}{(v * 100).quantize(Decimal('1'))}%"


def weak_points(rows: list[dict[str, Any]], as_of: date, *, target: Decimal, realistic: Decimal,
                undocumented: tuple[int, Decimal], unmatched_invoices: tuple[int, Decimal]) -> list[dict[str, Any]]:
    """The biggest gaps between the plan and the data, largest annual impact first (at most four)."""
    out: list[dict[str, Any]] = []
    if realistic < target:
        out.append({"area": "Revenue growth", "kind": "revenue", "team_id": "front",
                    "detail": f"At today's trend the company collects about €{realistic:,.0f} in 12 months, "
                              f"€{target - realistic:,.0f} short of the €{target:,.0f} expectation.",
                    "impact_annual": money(target - realistic)})
    for c in cost_growth(rows, as_of)[:2]:
        out.append({"area": f"{c['category'].replace('_', ' ').capitalize()} costs", "kind": "cost",
                    "category": c["category"], "team_id": c["team_id"],
                    "detail": f"Spend {pct(c['growth'])} over the last 6 months while revenue moved "
                              f"{pct(c['revenue_growth'])}.", "impact_annual": c["impact_annual"]})
    n, total = undocumented
    if n:
        out.append({"area": "Payments without documents", "kind": "documents", "team_id": None,
                    "detail": f"{n} outgoing payments (€{total:,.0f}) have no invoice or receipt, so their cost "
                              "can't be planned or challenged.", "impact_annual": money(total)})
    n, total = unmatched_invoices
    if n:
        out.append({"area": "Invoices not matched to payments", "kind": "invoices", "team_id": None,
                    "detail": f"{n} supplier invoices (€{total:,.0f}) have no matching bank payment yet.",
                    "impact_annual": money(total)})
    return sorted(out, key=lambda w: w["impact_annual"], reverse=True)[:4]


def action_for(w: dict[str, Any], plan: Plan) -> str:
    if w["kind"] == "revenue":
        return "Close the revenue gap: pick two growth experiments and report weekly orders and average ticket"
    if w["kind"] == "cost":
        return f"Cut {w['area'].lower()} back in line with revenue growth (target saving €{w['impact_annual']:,.0f}/yr)"
    if w["kind"] == "documents":
        return "Collect the missing invoices and receipts so every cost is in the plan"
    return "Reconcile supplier invoices with bank payments"


# -- store -------------------------------------------------------------------------------------------------------
def load(conn: Any) -> Plan | None:
    row = conn.execute("SELECT * FROM forecast_plan WHERE id").fetchone()
    if row is None:
        return None
    return Plan(annual_revenue_target=Decimal(str(row["annual_revenue_target"])), no_hiring=row["no_hiring"],
                max_new_monthly_cost=None if row["max_new_monthly_cost"] is None else Decimal(str(row["max_new_monthly_cost"])),
                guidance=row["guidance"], enabled=row["enabled"])


def save(conn: Any, plan: Plan, updated_by: str | None) -> None:
    conn.execute(
        "INSERT INTO forecast_plan (id, annual_revenue_target, no_hiring, max_new_monthly_cost, guidance, enabled, "
        "updated_by) VALUES (TRUE, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET "
        "annual_revenue_target = EXCLUDED.annual_revenue_target, no_hiring = EXCLUDED.no_hiring, "
        "max_new_monthly_cost = EXCLUDED.max_new_monthly_cost, guidance = EXCLUDED.guidance, "
        "enabled = EXCLUDED.enabled, updated_by = EXCLUDED.updated_by, updated_at = now()",
        (plan.annual_revenue_target, plan.no_hiring, plan.max_new_monthly_cost, plan.guidance, plan.enabled, updated_by))


def parse(body: dict[str, Any]) -> Plan:
    """Validate the CEO's settings; raises ValueError with a message fit for the dashboard."""
    try:
        target = money(body.get("annual_revenue_target"))
    except Exception:  # noqa: BLE001
        raise ValueError("Enter the annual revenue you expect, in euros.") from None
    if not target.is_finite() or target <= 0 or target > Decimal("1e11"):
        raise ValueError("The annual revenue target must be a positive amount.")
    cap = body.get("max_new_monthly_cost")
    if cap in (None, ""):
        cap_value = None
    else:
        try:
            cap_value = money(cap)
        except Exception:  # noqa: BLE001
            raise ValueError("The new monthly cost limit must be an amount in euros.") from None
        if not cap_value.is_finite() or cap_value < 0:
            raise ValueError("The new monthly cost limit can't be negative.")
    guidance = str(body.get("guidance") or "").strip()
    if len(guidance) > 4000:
        raise ValueError("Keep the guidance under 4,000 characters.")
    for key in ("no_hiring", "enabled"):
        if not isinstance(body.get(key, False), bool):
            raise ValueError(f"'{key}' must be true or false.")
    return Plan(target, bool(body.get("no_hiring", False)), cap_value, guidance, bool(body.get("enabled", True)))


def ceo_email(conn: Any, settings: Any) -> str | None:
    """The owner's sign-in email for this organization; the configured address for the legacy demo company."""
    from cfo.tenancy import current_tenant
    tenant = current_tenant(required=False)
    if tenant is not None and not getattr(tenant, "legacy", False):
        try:
            row = conn.execute("SELECT u.email FROM scout_auth.memberships m JOIN scout_auth.users u ON u.id = m.user_id "
                               "WHERE m.organization_id = %s AND m.role = 'owner' "
                               "ORDER BY (m.employee_id = %s) DESC, u.email LIMIT 1",
                               (tenant.id, tenant.employee_id or "")).fetchone()
        except Exception:  # noqa: BLE001 - no auth schema (tests, demo): fall back to the setting
            conn.rollback()
            row = None
        if row and row["email"]:
            return row["email"]
    return getattr(settings, "ceo_email", "") or None
