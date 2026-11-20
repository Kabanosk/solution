---
id: T030
title: API for the dashboard: cost breakdown by category, period summary, employees
status: doing
owner: coder
branch: task/T030-api-cost-breakdown
depends_on: ["T019"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_api_breakdown.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend python -m cfo.api.export_openapi docs/openapi.json && grep -q \"/v1/costs/by-category\" docs/openapi.json && grep -q \"/v1/employees\" docs/openapi.json"
    timeout: 900
protected: []
---
## Objective
New read endpoints that replace the hard-coded data in the front team's dashboard (`origin/feature/dashboard`, Rust/Yew; see `dashboard/src/data.rs` there: `FinancialSnapshot`, `ExpenseCategory`, `EMPLOYEES`, periods Year and Q4):
- `GET /v1/costs/by-category?from&to` — expense pie: items `{category_id, name, amount, share, by_month[]}` with categories `payroll`, `food_inventory` (Asimex, Fattoria, wholesale market), `rent_utilities`, `tax_social`, `partner_investment` (CHF tranches), `marketing`, `cloud_it`, `equipment`, `other`; plus `total_expenses`, `total_revenue`, `net`, `transactions` count for the period. One source of truth with `/v1/costs/by-team` (same bookings, different grouping; document the mapping in docs/api.md).
- `GET /v1/summary?from&to` (optional shortcut): revenue, expenses, net, transactions, top 3 categories, cost alerts in range.
- `GET /v1/employees` — id, name, role, team, manager, on_vacation today/as_of, monthly net salary, slack-linked flag; `payroll_total` monthly.
Same conventions as the rest of the API (money string decimal + currency, ISO dates, error model, `stub: false`), docs/api.md sections with curl + real responses, regenerated docs/openapi.json.

## Context
D-006, D-013, reports T002, T014, T018, T019, T020. Reuse T014 `monthly_cost_by_team` logic or the repo. Keep categories stable (the dashboard keys colors on `category_id`).

## Out of scope
Dashboard code; agent logic.
