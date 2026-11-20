---
id: T029
title: MLflow on the cluster: workbench, training run, promoted model, traces (runbook + do it)
status: todo
owner: null
branch: null
depends_on: ["T024", "T017", "T027"]
capabilities: []
acceptance:
  - id: AC-1
    run: "test -s docs/mlflow.md && grep -q FORECAST_MODEL_URI docs/mlflow.md && grep -q MLFLOW_TRACKING_URI docs/mlflow.md"
    timeout: 600
  - id: AC-2
    manual: "On the team cluster: workbench in project cfo-agent-uwur ran notebooks/forecast.ipynb; MLflow UI shows the training run and model liquidity-forecast@champion; API /v1/forecast/liquidity reports that mlflow_run_id; at least one agent trace in experiment cfo-agent. Judge records screenshots/URLs."
protected: []
---
## Objective
`docs/mlflow.md`: the click-by-click (and `oc`) path a teammate follows on the cluster, then a human or the
coordinator does it once and records evidence for the bonus:
1. Make `cfo-agent-uwur` a Data Science Project (if not already) and create a workbench (Jupyter Data Science
   image, small size, no GPU: the L4 belongs to vLLM) with env `MLFLOW_TRACKING_URI` (+ auth vars if the RHOAI
   MLflow needs them) and `DATABASE_URL` from Secret `postgres`; clone the repo; `uv`/`pip install -e backend[mlflow]`.
2. Run `notebooks/forecast.ipynb` → training run + registered model `liquidity-forecast@champion`.
3. Set `FORECAST_MODEL_URI=models:/liquidity-forecast@champion` and `MLFLOW_TRACKING_URI` in the app ConfigMap
   (T023), `oc rollout restart` API and worker; check `mlflow_run_id` in `/v1/forecast/liquidity`.
4. Trigger one golden-path event; open the trace in experiment `cfo-agent`.
5. Optional: `python -m cfo.eval --llm real` (T028) from the workbench → eval run.
Add the MLflow row and the vars to `INFRA.md`.

## Context
`.house/reports/mlflow-rhoai-research.md` (RHOAI 3.5 MLflow / workbench details), T024 manifests, INFRA.md,
docs/cluster-setup.md. Cluster budget: 1 node, keep the workbench ≤ 2 CPU / 8 GiB.

## Out of scope
Data Science Pipelines (not planned, see D-015), GPU training.
