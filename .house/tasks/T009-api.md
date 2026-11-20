---
id: T009
title: REST API for the dashboard (FastAPI + OpenAPI docs)
status: split  # -> T018, T019, T020
owner: null
branch: null
depends_on: ["T001", "T002"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_api.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend python -m cfo.api.export_openapi docs/openapi.json && test -s docs/openapi.json"
    timeout: 900
protected: []
---
## Objective
FastAPI app `cfo.api` with versioned read endpoints from `$HOUSE_DIR/demo-flow.md` (`/v1/kpis`, `/v1/forecast/liquidity`, `/v1/costs/by-team`, `/v1/cost-metrics`, `/v1/decisions`, `/v1/approvals` + `POST /v1/approvals/{id}:approve|reject`, `/v1/rules`, `/v1/events` SSE stream), CORS for the front, consistent error model, pagination on lists, OpenAPI exported to `docs/openapi.json`, `docs/api.md` with examples for Paweł and Piotrek. Front requests new endpoints via GitHub issues (D-010).

## Context
- Endpoints whose engine is not merged yet return clearly marked stub data from the repository layer, so the front can start now.

## Out of scope
Dashboard UI.
