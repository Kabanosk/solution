"""Teams, employees and the HR sheet (manager chain, vacations)."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from cfo.domain import Employee, Team, Vacation

HR_COLUMNS = ["id", "name", "role", "team", "manager", "net_monthly_salary_eur", "vacations"]

# Managers first (Repository.add_employee needs the manager row to exist).
EMPLOYEES: list[Employee] = [
    Employee(id="vincenzo", name="Vincenzo Amalfi", role="CEO / owner", team_id="front", manager_id=None),
    Employee(id="rosa", name="Rosa Pavese", role="Head chef (owns food costs)", team_id="kitchen", manager_id="vincenzo",
             vacations=[Vacation(start=date(2025, 8, 4), end=date(2025, 8, 15))]),
    Employee(id="grazyna", name="Grazyna Nowak", role="Office manager (invoices, bookkeeping)", team_id="front",
             manager_id="vincenzo",
             vacations=[Vacation(start=date(2025, 4, 14), end=date(2025, 4, 18)),
                        Vacation(start=date(2025, 12, 24), end=date(2026, 1, 2))]),
    Employee(id="yusuf", name="Yusuf Akcit", role="Pizza baker", team_id="kitchen", manager_id="rosa",
             vacations=[Vacation(start=date(2025, 9, 1), end=date(2025, 9, 12))]),
    Employee(id="vanessa", name="Vanessa Paradi", role="Waitstaff / marketing (temp)", team_id="front",
             manager_id="grazyna", vacations=[Vacation(start=date(2025, 7, 21), end=date(2025, 8, 1))]),
]

# Net salaries from the organizer's description sheet; Grazyna is not on the statement's payroll.
SALARY = {"vincenzo": "3200.00", "yusuf": "2350.00", "rosa": "2100.00", "vanessa": "950.00", "grazyna": ""}

TEAM_OWNERS = {"kitchen": "rosa", "front": "vincenzo", "marketing": "vanessa", "it": "vincenzo", "equipment": "yusuf"}
TEAM_NAMES = {
    "kitchen": "Kitchen / food",
    "front": "Front of house and admin",
    "marketing": "Marketing",
    "it": "IT / online-order app",
    "equipment": "Equipment",
}


def make_teams(budgets: dict[str, Decimal]) -> list[Team]:
    return [Team(id=t, name=TEAM_NAMES[t], owner_id=TEAM_OWNERS[t], monthly_budget=budgets.get(t)) for t in TEAM_NAMES]


def hr_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(HR_COLUMNS)
    for e in EMPLOYEES:
        vac = ";".join(f"{v.start.isoformat()}..{v.end.isoformat()}" for v in e.vacations)
        writer.writerow([e.id, e.name, e.role, e.team_id, e.manager_id or "", SALARY[e.id], vac])
    return out.getvalue()


def parse_hr_csv(text: str) -> list[Employee]:
    employees = []
    for row in csv.DictReader(io.StringIO(text)):
        vacations = []
        for part in filter(None, row["vacations"].split(";")):
            start, end = part.split("..")
            vacations.append(Vacation(start=date.fromisoformat(start), end=date.fromisoformat(end)))
        employees.append(Employee(id=row["id"], name=row["name"], role=row["role"], team_id=row["team"] or None,
                                  manager_id=row["manager"] or None, vacations=vacations))
    return employees
