---
id: T016
title: Liquidity gaps and credit line proposal
status: todo
owner: null
branch: null
depends_on: ["T015", "T003"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_liquidity_gaps.py"
    timeout: 900
protected: []
---
## Objective
`detect_gaps()` (predicted balance below 0 within the horizon) and `propose_credit_line(gap)` returning an APPROVAL action (amount with buffer, period, plain-language reason with top feature importances), classified via `cfo.agent.policy`. Part of former T006.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
