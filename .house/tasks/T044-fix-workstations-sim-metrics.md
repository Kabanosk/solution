---
id: T044
title: "Review fixes: workstation folder containment, simulator stepping, cost-metric edge cases"
status: done
owner: coder
branch: task/T044-fix-workstations-sim-metrics
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_review_fixes_t044.py backend/tests/test_mcp.py backend/tests/test_simulator.py backend/tests/test_cost_metrics.py"
    timeout: 900
protected: []
---
## Objective
Fix these Codex review findings (`$HOUSE_DIR/reports/review-fixes.md` items 20, 27 and P3 cost metrics), tests first
in the new file `backend/tests/test_review_fixes_t044.py`:
1. (20) `cfo/mcp_servers/workstations.py`: a path is allowed only when its fully resolved real path lies inside an
   allowed COMPANY folder of that workstation (a company-folder symlink pointing to a private sibling is denied
   and logged); `list_workstations` writes an `access_log` row; a path containing NUL is denied with
   `access_denied` (and logged without the NUL) instead of a database error.
2. (27) `cfo/sim/engine.py`: one `step` advances exactly one event (ties at the same timestamp are separate steps;
   a running playback's overdue events are not flushed by `step`); `start` rejects non-finite or non-positive speed.
3. (P3) `cfo/analysis/cost_metrics.py`: a cost rising while the metric stays at zero is flagged (no silent skip
   on division by zero); comparisons are only between consecutive CALENDAR months (a gap is not month-over-month).
   The December cloud +50% case must still be the only flag on the generated data.

## Context
Source reviews: `$HOUSE_DIR/reports/review-T004.md`, `review-T010.md`, `review-T007.md`. Confirm each at HEAD first;
D-004 (company folders only, every access logged) is part of the pitch.

## Out of scope
company_db.py (T040), agent graph (T041), API (T046).
