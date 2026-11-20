---
id: T031
title: Front dev kit: one-command local API, JSON examples, integration guide for the Rust dashboard
status: rejected  # misunderstood request, replaced by T032
owner: null
branch: null
depends_on: ["T030"]
capabilities: []
acceptance:
  - id: AC-1
    run: "test -s docs/dashboard-integration.md && test -s docs/api-examples/kpis.json && test -s docs/api-examples/costs-by-category.year.json && test -s docs/api-examples/forecast-liquidity.json"
    timeout: 900
  - id: AC-2
    run: "docker compose config --services | grep -qx api"
    timeout: 900
protected: []
---
## Objective
Make it trivial for Paweł (Rust/Yew/WASM dashboard on `origin/feature/dashboard`) to switch from the bundled organizer CSV to our API:
1. `docker-compose.yml`: services `seed` (one-shot: migrate + generate + load; reads SOURCE_CSV from a mounted local path given by env, never committed) and `api` (built from `Containerfile`, port 8000, CORS for `http://127.0.0.1:8080` Trunk dev server), so `docker compose up api` gives real data at http://localhost:8000. Keep Grzesiu's `db` and `files` services intact.
2. `scripts/export-api-examples.sh` that calls the running API and writes pretty JSON snapshots to `docs/api-examples/` (kpis, costs-by-category year + q4, costs-by-team, cost-metrics, forecast-liquidity, employees, decisions, approvals). Commit the snapshots: they contain only aggregates, never raw statement rows, so the dashboard can `include_str!` them as an offline fallback instead of the organizer CSV.
3. `docs/dashboard-integration.md`: base URLs (local, cluster Route placeholder), CORS, polling vs SSE `/v1/events`, a mapping table from the dashboard's current structs/screens to endpoints, ready-to-paste Rust serde structs for the main responses and a `gloo-net` fetch example with fallback to the bundled JSON, and a note that the organizer CSV must be removed from the public branch.

## Context
Reports T018-T023, T030. Do not modify anything under `dashboard/` (Paweł's branch).

## Out of scope
Dashboard code, cluster deployment.
