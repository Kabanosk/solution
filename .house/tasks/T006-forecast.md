---
id: T006
title: Liquidity and cost forecast (XGBoost, Jupyter, MLflow)
status: todo
owner: null
branch: null
depends_on: ["T002"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_forecast.py"
    timeout: 900
  - id: AC-2
    run: "test -f notebooks/forecast.ipynb"
    timeout: 900
protected: []
---
## Objective
`notebooks/forecast.ipynb` (runs on RHOAI Jupyter; D-009) trains XGBoost/tree models for daily net cash flow and monthly cost per team, logs params/metrics/model to MLflow when `MLFLOW_TRACKING_URI` is set. `cfo/forecast/` exposes `liquidity_forecast(days)` and `detect_gaps()` returning the predicted June 2026 dip with a plain-language explanation, and `propose_credit_line(gap)` producing an APPROVAL action for the agent.

## Context
- Seasonality matters: June shutdown (3 weeks no revenue) must be predicted for 2026 from 2025 history.
- Keep it simple and explainable (feature importances in the explanation).

## Out of scope
Slack, API routes.
