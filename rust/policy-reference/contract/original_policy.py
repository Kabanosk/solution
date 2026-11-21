"""Autonomy policy (D-005): decides whether the agent acts alone, acts and informs, or must ask the CEO.

`classify(action, context)` is a pure function. Order of evaluation, first hit wins:

1. CEO rule overrides (`Rule` rows; the newest matching active rule wins) beat every default below.
2. Strategic actions (credit, deposit, investment, FX hedge, team budget change): APPROVAL, any amount.
3. No money moves: AUTO.
4. Routine (known supplier, amount within +-20% of that supplier's typical amount): AUTO, any amount.
5. Amount < 1% of average monthly inflows: AUTO, except a new supplier, which is NOTIFY (never AUTO).
6. Amount > 5% of average monthly inflows (or inflows unknown): APPROVAL.
7. New supplier: APPROVAL.
8. Otherwise (known supplier, unusual amount, 1%..5% inclusive): NOTIFY.

Execution of APPROVAL actions is always simulated (that is the caller's job, see `Decision.executed_simulated`).
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from pydantic import BaseModel, Field

from cfo.domain import PolicyLevel, Rule, Transaction
from cfo.llm import LLM, Message, ToolSpec
from cfo.usage.recorder import usage_context

ROUTINE_TOLERANCE = Decimal("0.20")  # +-20% of the supplier's typical amount
AUTO_SHARE = Decimal("0.01")  # < 1% of average monthly inflows
APPROVAL_SHARE = Decimal("0.05")  # > 5% of average monthly inflows

STRATEGIC_ACTION_TYPES = frozenset(
    {"request_credit_line", "open_deposit", "make_investment", "fx_hedge", "change_team_budget"}
)


def _norm(value: str | None) -> str | None:
    value = " ".join((value or "").split()).casefold()  # case and spacing do not make a different name
    return value or None


class PolicyAction(BaseModel):
    """What the agent wants to do. `amount` is EUR (sign ignored); None when no money moves."""

    action_type: str  # e.g. "pay_invoice", "request_credit_line", "attach_invoice"
    amount: Decimal | None = None
    supplier: str | None = None
    category: str | None = None


class PolicyContext(BaseModel):
    """What the engine knows about the company. Build it with `context_from_transactions`."""

    avg_monthly_inflows: Decimal | None = None  # None or 0: thresholds unknown, so be conservative
    typical_amounts: dict[str, Decimal] = Field(default_factory=dict)  # known supplier -> typical amount
    rules: list[Rule] = Field(default_factory=list)  # CEO overrides


class PolicyDecision(BaseModel):
    level: PolicyLevel
    reason: str  # one human-readable sentence, shown on Slack and the dashboard


def _eur(value: Decimal) -> str:
    return f"{value:,.2f} EUR"


def _matching_rule(action: PolicyAction, rules: Iterable[Rule]) -> Rule | None:
    wanted = {
        "category": _norm(action.category),
        "supplier": _norm(action.supplier),
        "action_type": _norm(action.action_type),
    }
    best: tuple[int, Rule] | None = None
    for i, rule in enumerate(rules):
        if not rule.active:
            continue
        fields = {k: _norm(getattr(rule, k)) for k in wanted}
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields or any(wanted[k] != v for k, v in fields.items()):
            continue  # a rule without match fields never matches
        if best is None or (rule.created_at, i) >= (best[1].created_at, best[0]):
            best = (i, rule)
    return best[1] if best else None


def classify(action: PolicyAction, context: PolicyContext) -> PolicyDecision:
    rule = _matching_rule(action, context.rules)
    if rule is not None:
        return PolicyDecision(level=rule.level, reason=f'CEO rule "{rule.text}" applies, so this is {rule.level}.')

    if _norm(action.action_type) in STRATEGIC_ACTION_TYPES:
        what = action.action_type.replace("_", " ")
        return PolicyDecision(level=PolicyLevel.APPROVAL, reason=f"{what} always needs the CEO's approval.")

    if action.amount is None:
        return PolicyDecision(level=PolicyLevel.AUTO, reason="No money moves, so the agent acts on its own.")

    amount = abs(action.amount)
    supplier = _norm(action.supplier)
    typical = {_norm(k): abs(v) for k, v in context.typical_amounts.items()}
    known = supplier is not None and supplier in typical
    who = action.supplier or "this supplier"

    if known and typical[supplier] > 0:
        t = typical[supplier]
        if abs(amount - t) <= t * ROUTINE_TOLERANCE:
            return PolicyDecision(
                level=PolicyLevel.AUTO,
                reason=f"Routine payment: {_eur(amount)} to {who} is within 20% of the typical {_eur(t)}.",
            )

    inflows = context.avg_monthly_inflows
    share = amount / inflows if inflows and inflows > 0 else None
    if share is not None and share < AUTO_SHARE and not known:
        return PolicyDecision(
            level=PolicyLevel.NOTIFY,
            reason=f"New supplier {who}: {_eur(amount)} is below 1% of monthly inflows, so the agent acts and informs.",
        )
    if share is not None and share < AUTO_SHARE:
        return PolicyDecision(
            level=PolicyLevel.AUTO,
            reason=f"{_eur(amount)} is below 1% of average monthly inflows ({_eur(inflows)}).",
        )
    if share is None:
        return PolicyDecision(
            level=PolicyLevel.APPROVAL,
            reason=f"{_eur(amount)} to {who} is not routine and average monthly inflows are unknown, so the CEO decides.",
        )
    if share > APPROVAL_SHARE:
        return PolicyDecision(
            level=PolicyLevel.APPROVAL,
            reason=f"{_eur(amount)} is {share:.1%} of average monthly inflows, above the 5% limit.",
        )
    if not known:
        return PolicyDecision(
            level=PolicyLevel.APPROVAL,
            reason=f"New supplier {who}: first payments of at least 1% of monthly inflows need the CEO's approval.",
        )
    return PolicyDecision(
        level=PolicyLevel.NOTIFY,
        reason=(
            f"Unusual amount: {_eur(amount)} to {who} differs from the typical {_eur(typical[supplier])}"
            f" but is only {share:.1%} of monthly inflows, so the agent acts and informs."
        ),
    )


def context_from_transactions(transactions: Iterable[Transaction], rules: Sequence[Rule] = ()) -> PolicyContext:
    """Derive the context from the bank statement: typical amount = median outflow per counterparty,
    average monthly inflows = total inflows over the calendar months the statement spans."""
    txns = list(transactions)
    if not txns:
        return PolicyContext(rules=list(rules))
    first = min(t.booking_date for t in txns)
    last = max(t.booking_date for t in txns)
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    inflows = sum((t.amount for t in txns if t.amount > 0), Decimal(0))
    outflows: dict[str, list[Decimal]] = defaultdict(list)
    names: dict[str, str] = {}  # normalised name -> first spelling seen
    for t in txns:
        if t.amount < 0:
            key = _norm(t.counterparty) or t.counterparty
            names.setdefault(key, t.counterparty)
            outflows[key].append(-t.amount)
    return PolicyContext(
        avg_monthly_inflows=inflows / months,
        typical_amounts={names[key]: statistics.median(amounts) for key, amounts in outflows.items()},
        rules=list(rules),
    )


# Characterization-private implementation; the adapter is the public contract.
__all__ = []
