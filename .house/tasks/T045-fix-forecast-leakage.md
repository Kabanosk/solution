---
id: T045
title: "Review fixes: forecast backtest without holdout leakage, empty-data guards"
status: done
owner: coder
branch: task/T045-fix-forecast-leakage
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_forecast_leakage.py backend/tests/test_forecast_features.py backend/tests/test_forecast_model.py backend/tests/test_liquidity_gaps.py backend/tests/test_forecast_registry.py"
    timeout: 1200
protected: []
---
## Objective
Fix the forecast findings of the Codex review (`$HOUSE_DIR/reports/review-fixes.md` item 26 and P3 forecast), tests
first in the new file `backend/tests/test_forecast_leakage.py`:
1. (26) Shutdown windows and recurring due days are inferred only from the training window of each backtest split
   (`cfo/forecast/features.py:~230`, `cfo/forecast/model.py:~193`) and reused to build the holdout features. A test
   proves that changing holdout-only bookings does not change the training features. The API interval
   (`cfo/api/services.py`) uses the corrected RMSE. Report old vs new holdout error in the task report.
2. (P3) `load_daily_features()` on an empty `transactions` table returns an empty frame / clear error, not a
   `NaT` ValueError; `forecast_with` / `review_liquidity` with an explicit buffer works without fixed-cost rows.
The June dip and the D-013 numbers (buffer ≈ 17 730 €, oven what-if → credit line) must still hold.

## Context
T026 (MLflow registry) is merged; keep its logging/registration working. Source: `$HOUSE_DIR/reports/review-T014.md`,
`review-T015.md`, `review-T016.md`, `review-T019.md`. Confirm each at HEAD first.

## Out of scope
Notebook (T017), agent graph, API routes other than the interval value.
