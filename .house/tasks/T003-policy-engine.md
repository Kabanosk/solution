---
id: T003
title: Autonomy policy engine (AUTO / NOTIFY / APPROVAL)
status: todo
owner: null
branch: null
depends_on: ["T001"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_policy.py"
    timeout: 900
protected: []
---
## Objective
A pure function `classify(action, context) -> PolicyDecision(level, reason)` in `cfo/agent/policy.py` implementing D-005, plus CEO rule overrides stored as `Rule` rows.

## Context
- D-005: routine (known supplier, amount within ±20% of typical) → AUTO regardless of amount; < 1% of average monthly inflows → AUTO; unusual ≤ 5% → NOTIFY; > 5%, new supplier, credit, deposit/investment, FX hedge, team budget change → APPROVAL. CEO overrides (e.g. category `equipment` always APPROVAL) win. Execution of APPROVAL actions is always simulated.
- `reason` is a human-readable sentence (shown on Slack and the dashboard).
- Also: `parse_rule(text, llm)` turning a CEO sentence into a `Rule` (uses `cfo.llm`; tests with FakeLLM).

## Out of scope
LangGraph wiring, Slack.
