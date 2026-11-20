---
id: T026
title: Forecast model: register in MLflow, load from the registry
status: done
owner: coder
branch: task/T026-forecast-mlflow-registry
depends_on: ["T015"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend --extra mlflow pytest -q backend/tests/test_forecast_registry.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend pytest -q backend/tests/test_forecast_model.py backend/tests/test_api_read.py"
    timeout: 900
protected: []
---
## Objective
The forecast model travels through MLflow: whoever trains it (the RHOAI notebook T017, or the app itself)
registers it, and the API loads the version the team promoted.

- `cfo/forecast/model.py`: `log_to_mlflow_if_configured` returns the run id; when `register=True` it registers
  the booster as model `liquidity-forecast` and sets alias `champion` (configurable). The `.meta.json` sidecar
  (shutdown days, due days, metrics, params, trained_until) is logged as an artifact of the same run so that
  `ForecastModel` can be fully rebuilt from MLflow.
- `ForecastModel.load_from_mlflow(uri)` (e.g. `models:/liquidity-forecast@champion`) rebuilds the model from the
  booster + sidecar and keeps `mlflow_run_id` / `model_version` on the object.
- New setting `FORECAST_MODEL_URI` (in `cfo/config.py`, `.env.example`, INFRA.md §5 table). When set and
  `MLFLOW_TRACKING_URI` is set, `cfo/api/services.py::forecast_model` loads from MLflow (cached; re-resolve the
  alias at most every 60 s so a newly promoted version appears without a restart). Any MLflow error → log a
  warning and fall back to today's file/train-on-first-use path. The API fills `ForecastModelInfo.mlflow_run_id`
  and `version` from the loaded model.
- `backend/tests/test_forecast_registry.py`: real `mlflow` against a temporary `sqlite:///` tracking URI and a
  temp artifact dir (no server): train on the synthetic fixture → register → load by alias → predictions equal;
  API service picks the MLflow model and reports its run id; unreachable tracking URI → fallback, no exception.

## Context
- D-009 (forecast trained in Jupyter on RHOAI, tracked in MLflow). T015 report: `.house/reports/T015.md`.
- `mlflow` is an optional extra (`backend/pyproject.toml`); the base install must keep working without it.
- The notebook (T017) and the cluster setup (T029) will use exactly these functions.

## Out of scope
MLflow server deployment (T024), the notebook (T017), agent tracing (T027).
