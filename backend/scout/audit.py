"""Audit log of every connector read, DM and access decision, plus the access policy (deck "Built-in IAM")."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from scout.db import ConnFactory

EXEC_ROLES = frozenset({"ceo", "owner"})
FINANCE_ROLES = frozenset({"office_manager"})

Row = Mapping[str, Any]


def check(requester: str, role: str, target: str, resource: str) -> tuple[bool, str]:
    """May `requester` (with `role`) reach `resource` on `target`? Returns (allowed, policy).

    Targets: `laptop:<employee>`, `mail:<mailbox>`, `files:<employee>`, `chat:<channel>`, `bank`, `invoicing`.
    The CEO may reach anything; the office manager company files and mail; everyone else only their own laptop.
    """
    role = (role or "").casefold()
    if role in EXEC_ROLES:
        return True, "exec: all company data"
    kind, _, owner = target.partition(":")
    if kind in ("laptop", "files"):
        if owner == requester:
            return True, f"{owner}-devices: owner"
        if role in FINANCE_ROLES and owner != "vincenzo":
            return True, "company-folders: finance"
        return False, f"{owner}-devices: owner-or-exec-only"
    if kind in ("mail", "bank", "invoicing"):
        if role in FINANCE_ROLES:
            return True, f"{kind}: finance"
        return False, f"{kind}: finance-or-exec-only"
    if kind == "chat":
        return True, "chat: authorized channels"
    return False, "default: deny"


# -- salaries (D-029): the CEO sees every salary row, a manager the rows of everyone below them, everyone their own;
# the office manager gets the rest of the bank with other people's pay folded into ONE aggregate line.

SALARY_RE = re.compile(r"salary", re.IGNORECASE)
MIN_AGGREGATE = 2  # an "aggregate" of one person is that person's pay


def salary_sql(alias: str = "") -> str:
    """SQL twin of `is_salary_row` for a `transactions` table (optionally aliased)."""
    p = f"{alias}." if alias else ""
    return (f"(coalesce({p}category, '') = 'payroll' OR "
            f"position('salary' in lower({p}type || ' ' || {p}reference)) > 0)")


def is_salary_row(row: Row) -> bool:
    """A bank row that pays a person: payroll category, or "SALARY PAYMENT" / "Net Salary <name>" text.

    Company-level payroll costs (wage tax, social security) are not a person's pay and stay visible."""
    return (row.get("category") or "") == "payroll" or bool(SALARY_RE.search(f"{row.get('type') or ''} {row.get('reference') or ''}"))


def payee_of(row: Row, people: Iterable[Row]) -> str | None:
    """Whose pay is this row? The row's own `employee_id` if it has one, else the one employee whose full name is the
    counterparty (then the one named in the reference). None when unknown or ambiguous."""
    emp = row.get("employee_id")
    if emp:
        return str(emp)
    people = list(people)
    for text in (row.get("counterparty"), row.get("reference")):
        text = (text or "").casefold()
        hits = {p["id"] for p in people if p.get("name") and p["name"].casefold() in text}
        if len(hits) == 1:
            return next(iter(hits))
        if len(hits) > 1:
            return None
    return None


def reports_of(viewer: str, people: Iterable[Row]) -> set[str]:
    """Everyone whose `manager_id` chain leads to `viewer` (direct and indirect), never `viewer` itself."""
    people = list(people)
    manager = {p["id"]: p.get("manager_id") for p in people}
    out: set[str] = set()
    for p in people:
        seen, cur = set(), manager.get(p["id"])
        while cur and cur not in seen:
            if cur == viewer:
                out.add(p["id"])
                break
            seen.add(cur)
            cur = manager.get(cur)
    out.discard(viewer)
    return out


def can_see_salary(viewer: str, role: str, payee: str | None, reports: Iterable[str] = ()) -> tuple[bool, str]:
    """May `viewer` (with `role`, whose people below them are `reports`) see `payee`'s salary row?

    The office manager's right to the rest of the bank does NOT cover other people's pay (it is aggregated).
    An unknown payee counts as someone else."""
    if (role or "").casefold() in EXEC_ROLES:
        return True, "salary: exec sees all pay"
    if payee is None:
        return False, "salary: unknown payee is treated as someone else's pay"
    if payee == viewer:
        return True, "salary: own pay"
    if payee in set(reports):
        return True, "salary: manager of the payee"
    return False, "salary: other people's pay is exec-only"


@dataclass
class SalaryView:
    visible: list[Row]
    hidden: list[tuple[Row, str, str]] = field(default_factory=list)  # (row, DENY|MASK, policy)
    summary: str | None = None


def filter_salary_rows(rows: Sequence[Row], viewer: str, role: str, people: Sequence[Row]) -> SalaryView:
    """Drop (staff) or fold into one `Payroll (N employees): X EUR` line (office manager) the salary rows `viewer`
    may not see. Rows that are not salary rows pass through untouched."""
    people = list(people)
    reports = reports_of(viewer, people)
    role = (role or "").casefold()
    visible: list[Row] = []
    hidden: list[tuple[Row, str, str]] = []
    payees: set[str] = set()
    total = Decimal(0)
    for row in rows:
        if not is_salary_row(row):
            visible.append(row)
            continue
        payee = payee_of(row, people)
        ok, policy = can_see_salary(viewer, role, payee, reports)
        if ok:
            visible.append(row)
            continue
        if role in FINANCE_ROLES:
            hidden.append((row, "MASK", policy + "; folded into the payroll total"))
            payees.add(payee or f"?{row.get('counterparty')}")
            total += abs(Decimal(str(row.get("amount") or 0)))
        else:
            hidden.append((row, "DENY", policy))
    summary = None
    if role in FINANCE_ROLES and hidden:
        if len(payees) >= MIN_AGGREGATE:
            summary = f"Payroll ({len(payees)} employees): {total:,.2f} EUR"
        else:  # a lone payee cannot be summed without naming their pay
            policy = "salary: a one-person payroll total would reveal that person's pay"
            hidden = [(r, "DENY", policy) for r, _, _ in hidden]
    return SalaryView(visible, hidden, summary)


# -- task logs: a person sees the tasks they asked for or were asked to handle, the CEO sees all ---------------

def can_see_task(viewer: str, role: str, requested_by: str | None, owner_id: str | None) -> tuple[bool, str]:
    if (role or "").casefold() in EXEC_ROLES:
        return True, "tasks: exec sees all"
    if viewer in (requested_by, owner_id):
        return True, "tasks: requester or assignee"
    return False, "tasks: requester-or-exec-only"


class Audit:
    def __init__(self, conn_factory: ConnFactory):
        self._cf = conn_factory

    def record(self, actor: str, action: str, target: str, resource: str, decision: str = "ALLOW", *,
               task_id: str | None = None, requester: str | None = None, role: str | None = None,
               policy: str | None = None, reason: str | None = None) -> None:
        with self._cf() as conn:
            conn.execute(
                "INSERT INTO audit_log (task_id, actor, requester, role, action, target, resource, decision, policy, "
                "reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (task_id, actor, requester, role, action, target, resource[:500], decision, policy, reason),
            )
            conn.commit()

    def check(self, requester: str, role: str, target: str, resource: str, *, task_id: str | None = None) -> bool:
        allowed, policy = check(requester, role, target, resource)
        self.record("agent", "access_check", target, resource, "ALLOW" if allowed else "DENY", task_id=task_id,
                    requester=requester, role=role, policy=policy)
        return allowed

    def salary_filter(self, rows: Sequence[Row], viewer: str, role: str, people: Sequence[Row], *,
                      task_id: str | None = None) -> SalaryView:
        """`filter_salary_rows`, writing one `salary_filter` audit row (DENY or MASK) per row that was hidden."""
        view = filter_salary_rows(rows, viewer, role, people)
        for row, decision, policy in view.hidden:
            payee = payee_of(row, people) or "unknown payee"
            self.record("agent", "salary_filter", "bank", f"transaction {row.get('id') or '?'} ({payee})", decision,
                        task_id=task_id, requester=viewer, role=role, policy=policy)
        return view

    def check_task(self, viewer: str, role: str, task_id: str, requested_by: str | None, owner_id: str | None, *,
                   current_task: str | None = None) -> bool:
        allowed, policy = can_see_task(viewer, role, requested_by, owner_id)
        self.record("agent", "access_check", f"task:{task_id}", "task log", "ALLOW" if allowed else "DENY",
                    task_id=current_task, requester=viewer, role=role, policy=policy)
        return allowed
