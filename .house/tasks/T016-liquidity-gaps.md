---
id: T016
title: Liquidity gaps and credit line proposal
status: done
owner: coder
branch: task/T016-liquidity-gaps
depends_on: ["T015", "T003"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_liquidity_gaps.py"
    timeout: 900
protected: []
---
## Objective
`detect_gaps(buffer=...)` (predicted balance below a safety buffer, default one month of fixed costs; D-013), a what-if entry point `forecast_with(extra_outflows=[...])` for CEO questions like buying an oven, `propose_investment(surplus)` when the forecast stays well above the buffer (term deposit / money market fund sized so June stays above the buffer) and `propose_credit_line(gap)` returning an APPROVAL action (amount with buffer, period, plain-language reason with top feature importances), classified via `cfo.agent.policy`. Part of former T006.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
