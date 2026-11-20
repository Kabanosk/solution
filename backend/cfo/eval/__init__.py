"""Agent evaluation (T028, D-015): a fixed scenario set scored by deterministic checks and logged to MLflow.

    python -m cfo.eval --llm fake|real [--scenarios FILE] [--only id,id] [--tracking-uri URI]

* `scenarios.yaml`: the scenarios (seed state, steps, expectations), taken from the golden path
  (`.house/demo-flow.md`) and the autonomy rules D-005 / D-011 / D-013.
* `cfo.eval.scenarios`: loads and validates them.
* `cfo.eval.harness`: builds an isolated company (own Postgres schema, temporary workstation folders, recording
  notifier) per scenario and plays the steps through the real agent graph and policy engine.
* `cfo.eval.scoring`: the checks, plain functions of (outputs, expectations). No LLM judge.
* `cfo.eval.runner`: one MLflow run per evaluation (`mlflow.genai.evaluate`), one trace per scenario.

Only `runner` imports mlflow (optional extra: `uv sync --extra mlflow`).
"""
