---
id: T019
title: API read endpoints wired to the database
status: review
owner: coder
branch: task/T019-api-read
depends_on: ["T018", "T002"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_api_read.py"
    timeout: 900
protected: []
---
## Objective
Replace stubs with DB-backed data for `/v1/kpis`, `/v1/costs/by-team`, `/v1/cost-metrics`, `/v1/decisions`, `/v1/rules`; forecast endpoint stays a stub until T015 merges. Part of former T009.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
