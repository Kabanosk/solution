---
id: T020
title: API approvals and live events (SSE)
status: done
owner: coder
branch: task/T020-api-approvals-events
depends_on: ["T018", "T005"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_api_approvals.py"
    timeout: 900
protected: []
---
## Objective
`/v1/approvals` list, `POST /v1/approvals/{id}:approve|reject` calling the agent resume function, `/v1/events` SSE stream of new decisions/events. Part of former T009.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
