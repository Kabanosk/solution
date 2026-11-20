---
id: T049
title: Cost alert sentence always states the cost change and the metric change in EUR
status: done
owner: coder
branch: task/T049-cost-alert-wording
depends_on: ["T028", "T041"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_cost_alert_wording.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend --extra mlflow pytest -q backend/tests/test_agent_eval.py backend/tests/test_agent.py backend/tests/test_cost_metrics.py"
    timeout: 900
  - id: AC-3
    manual: "python -m cfo.eval --llm real --only cloud_cost_up_orders_flat_alert against the cluster Granite passes; coordinator records the run"
protected: []
---
## Objective
Demo step 4 says "cloud +50 % m/m, online orders +3 %". With the real Granite the alert sentence quoted only
the cost-per-order change (45.63 %) and called the amounts "units" (T028 report, re-measure section). Make the
cost-vs-metric alert reliably say: what cost, by how much it changed (percent and EUR before → now), how the
metric moved, and the cost per unit in EUR.

- `cfo/agent/investigate.py` facts: add `cost_before_eur`, `cost_now_eur`, `currency: "EUR"`, round percents to
  one decimal; when `cost_change_pct` is None (cost rising from 0, T044) say `new_cost: true` instead of a percent.
- Instruction to `explain`: lead with the cost change, then the metric change, then cost per unit; amounts in EUR.
- Guard (code, not prompt): if the LLM sentence does not contain the rounded cost change percent (or "new" for a
  new cost), use the deterministic `a.explanation` from `cfo.analysis.cost_metrics` instead; make sure that
  deterministic sentence itself contains the cost %, the metric %, and EUR.
- `backend/tests/test_cost_alert_wording.py`: FakeLLM that omits the cost % → fallback sentence with "50" and
  "EUR"; FakeLLM that includes it → kept; cost from 0 → "new cost" wording, no crash.

## Context
T007 (`cfo/analysis/cost_metrics.py`), T041 (investigate passes invoices; APPROVAL alerts go through
request_approval), T044 (`cost_change_pct` None for a cost rising from 0), T028 eval scenario
`cloud_cost_up_orders_flat_alert` and scorer `explanation_cites_amount`. Per-process test schema (D-016).

## Out of scope
Detection thresholds, other alert kinds, the eval scorers.
