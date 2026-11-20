---
id: T017
title: Jupyter notebook for RHOAI: train, evaluate, log to MLflow
status: todo
owner: null
branch: null
depends_on: ["T015"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend python -c \"import nbformat; nbformat.validate(nbformat.read('notebooks/forecast.ipynb', 4))\""
    timeout: 900
protected: []
---
## Objective
`notebooks/forecast.ipynb` that calls T014/T015 code, shows charts, evaluates on a holdout month and logs to MLflow (bonus: Jupyter + MLflow on RHOAI). Part of former T006.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
