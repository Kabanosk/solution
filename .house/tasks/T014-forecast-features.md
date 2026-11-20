---
id: T014
title: Forecast features: daily cash flow and monthly cost per team series
status: review
owner: coder
branch: task/T014-forecast-features
depends_on: ["T002"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_forecast_features.py"
    timeout: 900
protected: []
---
## Objective
Functions in `backend/cfo/forecast/features.py` that read transactions/invoices from the DB and return (a) daily net cash flow with calendar features (weekday, month, holiday/shutdown flag) and (b) monthly cost per team. Part of former T006.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
