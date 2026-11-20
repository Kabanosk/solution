---
id: T007
title: Cost vs metric anomaly detection (3 pairs)
status: done
owner: coder
branch: task/T007-cost-metrics
depends_on: ["T002"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_cost_metrics.py"
    timeout: 900
protected: []
---
## Objective
`cfo/analysis/cost_metrics.py`: monthly series for the three D-006 pairs (food cost ↔ orders, ads ↔ new customers, cloud ↔ online orders), per-unit cost, and `detect_anomalies()` flagging cost growth without metric growth, with the top contributing invoice line items and a one-sentence explanation. Must flag the generated cloud +50% month.

## Out of scope
Agent wiring, Slack.
