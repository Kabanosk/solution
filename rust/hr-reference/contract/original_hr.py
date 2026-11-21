from __future__ import annotations
from datetime import date
from typing import Any
ROLE_CODES=(('ceo', 'ceo'), ('owner', 'ceo'), ('office manager', 'office_manager'), ('chef', 'head_chef'), ('baker', 'baker'), ('waitstaff', 'waitstaff'), ('marketing', 'waitstaff'))
class HR:
    """People, roles, vacations and the manager chain (company HR tables)."""

    def __init__(self, conn_factory: ConnFactory, user_map: dict[str, str] | None = None,
                 slack_names: dict[str, str] | None = None):
        self._cf = conn_factory
        self._by_slack = {v: k for k, v in (user_map or {}).items()}
        self._slack_names = dict(slack_names or {})  # employee id -> the real person's Slack name

    def employee(self, employee_id: str) -> dict[str, Any] | None:
        with self._cf() as conn:
            return conn.execute("SELECT * FROM employees WHERE id = %s", (employee_id,)).fetchone()

    def by_slack_id(self, slack_id: str) -> dict[str, Any] | None:
        if slack_id in self._by_slack:
            return self.employee(self._by_slack[slack_id])
        with self._cf() as conn:
            return conn.execute("SELECT * FROM employees WHERE slack_user_id = %s", (slack_id,)).fetchone()

    def all(self) -> list[dict[str, Any]]:
        with self._cf() as conn:
            return conn.execute("SELECT * FROM employees ORDER BY id").fetchall()

    def name(self, employee_id: str | None) -> str:
        if not employee_id:
            return "someone"
        if self._slack_names.get(employee_id):
            return self._slack_names[employee_id]  # a real person on Slack plays this role
        e = self.employee(employee_id)
        return ((e or {}).get("name") or "").split()[:1][0] if (e or {}).get("name") else employee_id

    def link_slack(self, user_map: dict[str, str], names: dict[str, str]) -> None:
        """Write who plays which role on Slack into the HR table, so the dashboard shows the same people."""
        with self._cf() as conn:
            from scout.slack_directory import _ensure
            _ensure(conn)
            # People who registered themselves on Slack keep their link; only the configured role map is reset.
            conn.execute("UPDATE employees SET slack_user_id = NULL, slack_name = NULL WHERE NOT EXISTS "
                         "(SELECT 1 FROM slack_members m WHERE m.employee_id = employees.id "
                         "AND m.status = 'registered')")
            for emp, uid in user_map.items():
                conn.execute("UPDATE employees SET slack_user_id = %s, slack_name = %s WHERE id = %s",
                             (uid or None, names.get(emp), emp))
            conn.commit()

    def full_name(self, employee_id: str) -> str:
        """The whole name: the linked Slack person's, else the HR record's."""
        if self._slack_names.get(employee_id):
            return self._slack_names[employee_id]
        e = self.employee(employee_id) or {}
        return e.get("slack_name") or e.get("name") or employee_id

    def role(self, employee_id: str) -> str:
        text = ((self.employee(employee_id) or {}).get("role") or "").casefold()
        return next((code for needle, code in ROLE_CODES if needle in text), "staff")

    def on_vacation(self, employee_id: str, day: date) -> bool:
        with self._cf() as conn:
            row = conn.execute("SELECT 1 FROM vacations WHERE employee_id = %s AND start_date <= %s AND end_date >= %s",
                               (employee_id, day, day)).fetchone()
        return row is not None

    def manager(self, employee_id: str) -> str | None:
        return (self.employee(employee_id) or {}).get("manager_id")

    def chain(self, employee_id: str) -> list[str]:
        out, cur = [], employee_id
        while cur and cur not in out:
            out.append(cur)
            cur = self.manager(cur)
        return out

    def first_available(self, employee_id: str, day: date) -> tuple[str, list[str]]:
        """The first person in the chain who is not on vacation, and who was skipped."""
        chain = self.chain(employee_id)
        for i, who in enumerate(chain):
            if not self.on_vacation(who, day):
                return who, chain[:i]
        return chain[-1], chain[:-1]

__all__=[]
