---
id: T050
title: Scout access control for salaries and task logs (hard guardrails in code)
status: todo
owner: coder
branch: task/T050-scout-access
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/scout/test_salary_access.py backend/tests/scout/test_task_log_access.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend pytest -q backend/tests/scout"
    timeout: 1800
protected: []
---
## Objective
Scout must never show a person's salary to someone who may not see it, and must not hand anyone else's task log
to a non-CEO. These are the deterministic controls; the NeMo rails (T051/T052) are only a second layer. Rules
agreed with Wojtek on 2026-11-21 (D-029):

1. **Salary rows** (bank transactions that pay a salary: payroll category / "SALARY PAYMENT" / "Net Salary <name>"):
   - CEO/owner: sees every salary row.
   - Manager: sees salary rows of the people whose HR `manager_id` chain leads to them (direct and indirect
     reports), plus their own.
   - Everyone: sees their own salary row(s).
   - Office manager (finance role, sees the rest of the bank): salary rows of others are replaced by ONE aggregate
     line per query, e.g. `Payroll (N employees): X EUR`, unless the rules above (own / reports) let them see a row.
   - Everyone else gets no salary rows of other people and no payroll totals.
   - Applies to every tool that can return bank rows or bank-derived numbers: at least `search_payments`,
     `missing_invoices`, `cash_position`, `cost_trends`, `team_budgets` (check each; an aggregate that reveals a
     single person's pay must not reach staff). Staff keep no general bank access (`audit.check` stays as is);
     they only gain their own / their reports' salary rows.
   - Every hidden or aggregated salary row writes one `audit_log` row (action `salary_filter`, decision `DENY` or
     `MASK`, policy name) so the dashboard Audit page shows it.
2. **`task_log` / `scout_tasks`:** a non-CEO only sees tasks where they are the requester (or assignee if the
   task model has one); CEO/owner sees all. Denials are audited like other access checks.
3. Put the policy in one place (`scout/audit.py` next to `check`, e.g. `can_see_salary(viewer, role, payee)`),
   with the payee resolved from the transaction (employee id if present, else the name in the description matched
   against HR). Unknown payee on a salary row ⇒ treat it as someone else's salary.

## Context
- Access policy today: `backend/scout/audit.py` (`check`, `Audit.check`, `EXEC_ROLES`, `FINANCE_ROLES`).
- Tools: `backend/scout/loops/toolbox.py` (`search_payments` ~:161, `scout_tasks` ~:241, `task_log` ~:253,
  `cost_trends`, `team_budgets`); roles and manager chain: `backend/scout/loops/context.py` (`HR.role`,
  `HR.manager`, `ROLE_CODES`).
- Salary rows in the data: `backend/cfo/data/source.py` (~:64), generated bank history.
- Tests: `backend/tests/scout/test_salary_access.py`, `backend/tests/scout/test_task_log_access.py` (fixtures in
  `backend/tests/scout/conftest.py`). Write them first (TDD).

## Out of scope
NeMo, prompts, Slack wording, approvals (T051/T052). Do not change the HR or bank seed data.
