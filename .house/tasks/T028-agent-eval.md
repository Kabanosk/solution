---
id: T028
title: Agent evaluation in MLflow (scenario set + deterministic scorers)
status: merged
owner: coder
branch: task/T028-agent-eval
depends_on: ["T027"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend --extra mlflow pytest -q backend/tests/test_agent_eval.py"
    timeout: 900
  - id: AC-2
    manual: "python -m cfo.eval against the cluster vLLM logs an evaluation run in MLflow (metrics + per-scenario table); judge records the run URL and scores"
protected: []
---
## Objective
A repeatable evaluation of the agent that a judge can open in MLflow: "how often does it pick the right
autonomy level and explain itself with real numbers".

- `backend/cfo/eval/scenarios.yaml`: 10–15 scenarios taken from the golden path and D-005/D-011 rules, each an
  input event (+ seed state) and expectations: routine payment → AUTO; unusual ≤ 5 % → NOTIFY; > 5 % → APPROVAL;
  new vendor small → NOTIFY (D-011); missing invoice found on the workstation → attached; amount mismatch →
  clarification; nothing found → DM escalation chain; June forecast dip → investment / credit-line proposal
  (D-013); "execution is always SIMULATED".
- Deterministic scorers (no LLM judge): `policy_level_correct`, `action_correct`, `no_execution_without_approval`,
  `explanation_cites_amount` (the EUR amount or % appears in the user-facing text), `latency_s`. Optionally one
  LLM-as-judge scorer behind a flag, using the same `LLM_BASE_URL` (open-weights only).
- `python -m cfo.eval [--llm fake|real] [--scenarios …]`: runs every scenario through the real graph, logs to
  MLflow via `mlflow.genai.evaluate` (or `mlflow.evaluate` + a results table if the GenAI API does not fit),
  experiment `cfo-agent-eval`, tags `llm_model`, `git_sha`. Traces from T027 are linked to the eval rows.
- `backend/tests/test_agent_eval.py`: run the suite with `FakeLLM` against a temp `sqlite:///` tracking URI;
  the run exists, has the aggregate metrics, and a deliberately wrong expectation scores 0.

## Context
D-005, D-011, D-013, `.house/demo-flow.md`, T027 (`cfo/observability.py`). Research notes:
`.house/reports/mlflow-rhoai-research.md`.

## Out of scope
Changing agent behaviour to pass the eval (report failures instead), deployment.
