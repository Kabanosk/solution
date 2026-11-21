"""Audit log of every connector read, DM and access decision, plus the access policy (deck "Built-in IAM")."""

from __future__ import annotations

import re
import unicodedata
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


def _tokens(text: str | None) -> tuple[str, ...]:
    """Case, spacing and diacritics normalised words of `text`."""
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    return tuple(re.findall(r"[a-z0-9]+", plain))


def _names_in(tokens: tuple[str, ...], people: list[Row]) -> set[str]:
    out = set()
    for p in people:
        name = _tokens(p.get("name"))
        if name and any(tokens[i:i + len(name)] == name for i in range(len(tokens) - len(name) + 1)):
            out.add(p["id"])
    return out


def payee_of(row: Row, people: Iterable[Row]) -> str | None:
    """Whose pay is this row? The one employee whose full name IS the counterparty (whole name, normalised; never a
    substring: "Yusuf Akcit Jr." is nobody) or whom `employee_id` names. A reference that names someone else, an
    unknown name or two employees with that name all give None, which is treated as someone else's pay."""
    people = list(people)
    who: set[str] = {str(row["employee_id"])} if row.get("employee_id") else set()
    cp = _tokens(row.get("counterparty"))
    who |= {p["id"] for p in people if cp and _tokens(p.get("name")) == cp}
    if len(who) != 1 or _names_in(_tokens(row.get("reference")), people) - who:
        return None
    return next(iter(who))


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
    summaries: list[str] = field(default_factory=list)  # office manager: one `Payroll YYYY-MM (N employees): X EUR` per month


def _month(row: Row) -> str:
    d = row.get("booking_date")
    return f"{d:%Y-%m}" if hasattr(d, "strftime") else str(d)[:7]


def filter_salary_rows(rows: Sequence[Row], viewer: str, role: str, people: Sequence[Row], *,
                       fold: bool = True) -> SalaryView:
    """Drop (staff) or fold into `Payroll YYYY-MM (N employees): X EUR` lines (office manager) the salary rows `viewer`
    may not see. Rows that are not salary rows pass through untouched.

    The totals are per whole calendar month, so differencing two queries only ever gives a month's total; callers
    must therefore hand in every salary row of each month they include. With `fold=False` (a caller that shows no
    totals) the office manager's hidden rows are plain DENY. A month with fewer than `MIN_AGGREGATE`
    hidden people is dropped (DENY): a total over one person is that person's pay."""
    people = list(people)
    reports = reports_of(viewer, people)
    role = (role or "").casefold()
    visible: list[Row] = []
    hidden: list[tuple[Row, str, str]] = []
    months: dict[str, list[tuple[Row, str]]] = {}  # month -> (row, payee key) of rows to fold
    for row in rows:
        if not is_salary_row(row):
            visible.append(row)
            continue
        payee = payee_of(row, people)
        ok, policy = can_see_salary(viewer, role, payee, reports)
        if ok:
            visible.append(row)
        elif role in FINANCE_ROLES and fold:
            months.setdefault(_month(row), []).append((row, payee or f"?{row.get('counterparty')}"))
            hidden.append((row, "MASK", policy + "; folded into the monthly payroll total"))
        else:
            hidden.append((row, "DENY", policy))
    summaries: list[str] = []
    too_few = "salary: a one-person payroll total would reveal that person's pay"
    for month in sorted(months, reverse=True):
        payees = {k for _, k in months[month]}
        if len(payees) >= MIN_AGGREGATE:
            total = sum((abs(Decimal(str(r.get("amount") or 0))) for r, _ in months[month]), Decimal(0))
            summaries.append(f"Payroll {month} ({len(payees)} employees): {total:,.2f} EUR")
        else:
            ids = {id(r) for r, _ in months[month]}
            hidden = [(r, "DENY", too_few) if id(r) in ids else (r, d, pol) for r, d, pol in hidden]
    return SalaryView(visible, hidden, summaries)


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
                      task_id: str | None = None, fold: bool = True) -> SalaryView:
        """`filter_salary_rows`, writing one `salary_filter` audit row (DENY or MASK) per row that was hidden."""
        view = filter_salary_rows(rows, viewer, role, people, fold=fold)
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
