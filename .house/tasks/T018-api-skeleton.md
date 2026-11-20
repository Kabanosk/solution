---
id: T018
title: API skeleton: FastAPI app, error model, OpenAPI export, docs, stub endpoints
status: done
owner: coder
branch: task/T018-api-skeleton
depends_on: ["T001"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_api_skeleton.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend python -m cfo.api.export_openapi docs/openapi.json && test -s docs/openapi.json"
    timeout: 900
protected: []
---
## Objective
FastAPI app `cfo.api` with all endpoints from `$HOUSE_DIR/demo-flow.md` returning clearly marked stub data, consistent error model, pagination, CORS, `/healthz`, OpenAPI export and `docs/api.md` with examples for the front team. Lets Paweł and Piotrek start now. Part of former T009.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
