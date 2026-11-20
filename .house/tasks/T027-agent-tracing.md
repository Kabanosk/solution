---
id: T027
title: MLflow tracing of the agent (LangGraph + LLM calls)
status: review
owner: coder
branch: task/T027-agent-tracing
depends_on: ["T005"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend --extra mlflow pytest -q backend/tests/test_tracing.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend pytest -q backend/tests"
    timeout: 900
protected: []
---
## Objective
Every agent run is visible as an MLflow trace (Red Hat bonus: "Use MLflow for tracing and evaluating the agents").

- `cfo/observability.py::init_tracing(settings)`: no-op unless `MLFLOW_TRACKING_URI` is set and `mlflow` is
  importable. Otherwise sets experiment `cfo-agent` (setting `MLFLOW_AGENT_EXPERIMENT`), enables autologging
  for the `openai` client (that is what `cfo/llm.py::OpenAICompatLLM` uses) and for LangGraph/LangChain if it
  produces useful spans. Idempotent; never raises (warning only).
- `Agent.handle(event)` in `cfo/agent/graph.py` becomes one root span per event (`@mlflow.trace` or a span
  context manager behind a tiny wrapper that is a no-op without MLflow) with tags: event type, transaction id,
  final policy level (AUTO / NOTIFY / APPROVAL), outcome, simulated execution yes/no. Graph nodes appear as child
  spans (via autolog or explicit spans around node functions); LLM calls appear under the node that made them.
  Sub-agent investigations (`cfo/agent/investigate.py`) are child spans too.
- Call `init_tracing` from the API app startup and expose it for the worker entrypoint (T011 owns `cfo.run`;
  leave a one-line note in the report for it).
- `backend/tests/test_tracing.py`: temp `sqlite:///` tracking URI, run the graph on one golden-path event with
  `FakeLLM`, then `mlflow.search_traces` finds one trace with the expected tags and at least one child span per
  visited node; with `MLFLOW_TRACKING_URI` unset nothing is imported and the test suite still passes.

## Context
- D-008 (LangGraph, model-agnostic `LLM_BASE_URL`), D-009. Golden path: `.house/demo-flow.md`.
- Use MLflow ≥ 3 (GenAI tracing); bump the optional extra in `backend/pyproject.toml` and `uv lock`.
- Read `.house/reports/mlflow-rhoai-research.md` if it exists (autolog details, auth env vars).

## Out of scope
Evaluation datasets and scorers (T028), forecast model logging (T026), deploying MLflow (T024).
