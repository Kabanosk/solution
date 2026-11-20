# Review T015

- **Reviewer:** codex
- **Note:** per-task review of a merged task (coordinator script, user asked for Codex on everything; daily budget overridden)
- **Base:** e667bf38a8ed51ecfd008a1ba30c3d5b4ca5403d
- **Head:** f081f10d27a6b724b1a35f427e78425109c0104c
- **Status:** ok
- **Blockers:** 0
- **Majors:** 2
- **Minors:** 0
- **Nits:** 0
- **Verdict:** Request changes. Static review found a leaky backtest and inactive MLflow logging in OpenShift, with no confirmed forecast runtime crash. AC-1 and live deployment were not run.
- **Date:** 2026-11-20 15:29

Treat the findings as an outside opinion, not necessarily right: fix real bugs, log disagreements.

## Review text

MAJOR backend/cfo/forecast/model.py:193 — The backtest splits the data after inferring shutdown windows and recurring payment dates from the full transaction history. Holdout bookings can therefore influence both training and test features, making the reported error unreliable. Infer those features from pre-cutoff bookings and reuse them to score the holdout.

MAJOR backend/cfo/forecast/model.py:227 — OpenShift sets `MLFLOW_TRACKING_URI`, but the backend image installs dependencies without the `mlflow` extra. Forecast training continues, but the deployed app skips logging parameters, metrics, and the model even with tracking configured.

VERDICT: Request changes. Static review found a leaky backtest and inactive MLflow logging in OpenShift, with no confirmed forecast runtime crash. AC-1 and live deployment were not run.
