---
id: T017
title: Jupyter notebook for RHOAI: train, evaluate, log to MLflow
status: todo
owner: null
branch: null
depends_on: ["T015", "T026"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend python -c \"import nbformat; nbformat.validate(nbformat.read('notebooks/forecast.ipynb', 4))\""
    timeout: 900
protected: []
---
## Objective
`notebooks/forecast.ipynb`, meant to run in an RHOAI workbench in our Data Science Project (bonus: Jupyter +
MLflow on RHOAI). It only calls library code (T014/T015/T026), so the notebook stays thin:
1. Load transactions from Postgres (`DATABASE_URL`, in-cluster `postgres:5432`) with a fallback to the bank CSV
   (`SOURCE_CSV`, or `http://files:8080/` in the cluster).
2. Explore: daily net cash flow, monthly totals, the June shutdown (charts).
3. Train with `train(...)`, evaluate on the 60-day holdout vs the naive baseline (chart actual vs predicted),
   show feature importances / SHAP for June 2026.
4. Forecast 2026 balance with the safety buffer line (D-013).
5. Log + register in MLflow (`register=True` from T026, alias `champion`) when `MLFLOW_TRACKING_URI` is set;
   print the run URL. A markdown cell explains how the API picks the new version up (`FORECAST_MODEL_URI`).
Commit the notebook with outputs cleared. Part of former T006.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
