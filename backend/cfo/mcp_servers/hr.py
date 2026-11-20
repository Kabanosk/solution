"""`hr` MCP server: org chart, managers, vacations, escalation chain (D-003). Read-only."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from cfo.db.repo import Repository
from cfo.domain import Employee
from cfo.mcp_servers.common import make_repo_factory, parse_date, to_data
from mcp.server.fastmcp import FastMCP

MAX_CHAIN = 20


class HrTools:
    def __init__(self, repo_factory: Callable[[], Repository]):
        self._repo_factory = repo_factory

    def _all(self) -> dict[str, Employee]:
        with self._repo_factory() as repo:
            return {e.id: e for e in repo.list_employees()}

    def _day(self, on: str | None) -> date:
        return parse_date(on) or date.today()

    @staticmethod
    def _view(e: Employee, day: date) -> dict[str, Any]:
        row = to_data(e)
        row["on_vacation"] = e.on_vacation(day)
        return row

    def org_chart(self, on: str | None = None) -> list[dict[str, Any]]:
        """All employees with manager, direct reports and vacation status on `on` (YYYY-MM-DD, default today)."""
        day = self._day(on)
        emps = self._all()
        out = []
        for e in emps.values():
            row = self._view(e, day)
            row["direct_reports"] = sorted(
                x.id for x in emps.values() if x.manager_id == e.id
            )
            out.append(row)
        return out

    def get_employee(
        self, employee_id: str, on: str | None = None
    ) -> dict[str, Any] | None:
        e = self._all().get(employee_id)
        return self._view(e, self._day(on)) if e else None

    def manager_of(
        self, employee_id: str, on: str | None = None
    ) -> dict[str, Any] | None:
        """The employee's direct manager, or null for the CEO / unknown employee."""
        emps = self._all()
        e = emps.get(employee_id)
        m = emps.get(e.manager_id) if e and e.manager_id else None
        return self._view(m, self._day(on)) if m else None

    def is_on_vacation(self, employee_id: str, on: str | None = None) -> dict[str, Any]:
        e = self._all().get(employee_id)
        if e is None:
            return {
                "ok": False,
                "error": "unknown_employee",
                "employee_id": employee_id,
            }
        day = self._day(on)
        return {
            "ok": True,
            "employee_id": employee_id,
            "date": day.isoformat(),
            "on_vacation": e.on_vacation(day),
            "vacations": [v.model_dump(mode="json") for v in e.vacations],
        }

    def who_is_on_vacation(self, on: str | None = None) -> list[dict[str, Any]]:
        day = self._day(on)
        return [self._view(e, day) for e in self._all().values() if e.on_vacation(day)]

    def escalation_chain(
        self, employee_id: str, on: str | None = None
    ) -> dict[str, Any]:
        """Who to ask about something owned by `employee_id` on a date: the employee first, then managers up to
        the CEO. `first_available` is the first one not on vacation (null if everybody is away).
        """
        day = self._day(on)
        emps = self._all()
        if employee_id not in emps:
            return {
                "ok": False,
                "error": "unknown_employee",
                "employee_id": employee_id,
            }
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        cur: Employee | None = emps[employee_id]
        while cur is not None and cur.id not in seen and len(chain) < MAX_CHAIN:
            seen.add(cur.id)
            chain.append(self._view(cur, day))
            cur = emps.get(cur.manager_id) if cur.manager_id else None
        first = next((c["id"] for c in chain if not c["on_vacation"]), None)
        return {
            "ok": True,
            "date": day.isoformat(),
            "chain": chain,
            "first_available": first,
        }


def build_server(
    repo_factory: Callable[[], Repository] | None = None,
    host: str = "127.0.0.1",
    port: int = 8003,
) -> FastMCP:
    tools = HrTools(repo_factory or make_repo_factory())
    server = FastMCP(
        "hr",
        instructions="Org chart, managers and vacations of Pizzeria Amalfi staff. Read-only.",
        host=host,
        port=port,
    )
    for fn in (
        tools.org_chart,
        tools.get_employee,
        tools.manager_of,
        tools.is_on_vacation,
        tools.who_is_on_vacation,
        tools.escalation_chain,
    ):
        server.tool()(fn)
    return server
