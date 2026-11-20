---
id: T015
title: Forecast model: XGBoost liquidity forecast with optional MLflow logging
status: todo
owner: null
branch: null
depends_on: ["T014"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_forecast_model.py"
    timeout: 900
protected: []
---
## Objective
`backend/cfo/forecast/model.py`: train (XGBoost or sklearn tree) on T014 features, save/load the model file, `liquidity_forecast(days)` returning daily predicted balance. Logs params/metrics/model to MLflow only when `MLFLOW_TRACKING_URI` is set. The June shutdown must reappear in the 2026 forecast. Part of former T006.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
