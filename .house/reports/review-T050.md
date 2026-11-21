# Review T050

- **Reviewer:** codex
- **Note:** Codex reviews Claude's work (3/40 today)
- **Base:** 35679a2626eeb507d2314626aea3004023c5ea0a
- **Head:** f4484a13c3e92f96e9f54a9b2fce64b6d2dcbe44
- **Status:** ok
- **Blockers:** 2
- **Majors:** 3
- **Minors:** 1
- **Nits:** 0
- **Verdict:** Request changes. The diff leaves salary disclosure paths and a task-log authorization race. Acceptance tests were not run under the review-only instructions.
- **Date:** 2026-11-21 01:23

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

BLOCKER backend/scout/loops/toolbox.py:268 — `cash_position` includes hidden salaries in exact bank totals. When the 30-day window contains one hidden salary, the office manager can recover it by subtracting the visible outflows. The single `MASK` event also fails the per-row audit requirement.
BLOCKER backend/scout/audit.py:72 — Payee resolution grants access on a substring match. If HR contains “Yusuf Akcit” but a salary row names “Yusuf Akcit Jr.”, who has no HR match, Scout exposes that row to Yusuf as his own salary.
MAJOR backend/scout/loops/toolbox.py:302 — `team_budgets` aggregates only debit salary rows, while `search_payments` also includes credits in its payroll summary. When both totals are published for the same month, subtracting them can expose an individual payroll reversal.
MAJOR backend/scout/loops/toolbox.py:337 — `task_log` checks ownership before and after reading steps in separate statements. If a task is reassigned away and back between those checks, the response can include steps written while the requester had no access.
MAJOR backend/scout/audit.py:145 — The office manager can receive one payroll line per month from a single query, exceeding the agreed limit of one aggregate line per query.
MINOR backend/scout/loops/toolbox.py:236 — `missing_invoices` can audit hidden rows as `MASK` even though it discards the payroll summary and returns none of those rows. Those audit entries should be `DENY`.

VERDICT: Request changes. The diff leaves salary disclosure paths and a task-log authorization race. Acceptance tests were not run under the review-only instructions.
