---
id: T048
title: "Cluster smoke check: Granite tool calling and the three MCP servers"
status: review
owner: coder
branch: task/T048-cluster-smoke-llm-mcp
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_smoke.py"
    timeout: 600
  - id: AC-2
    run: "uv run --project backend python -m cfo.smoke --help"
  - id: AC-3
    manual: "Run inside the cluster (oc rsh deploy/api cfo smoke or a Job) and paste the PASS output into reports/T048.md"
protected: []
---
## Objective
One command, `cfo smoke` / `python -m cfo.smoke`, that a human or the judge runs inside the cluster to prove the
moving parts work, printing one PASS/FAIL line per check and exiting non-zero on any FAIL:
1. LLM: `GET {LLM_BASE_URL}/models` lists `LLM_MODEL`; a chat request WITH `tools` returns `tool_calls` (native
   tool calling enabled); if it returns HTTP 400 about tools, FAIL with the hint
   `--enable-auto-tool-choice --tool-call-parser=hermes`; also report whether the T041 fallback path works.
2. MCP: for each `MCP_*_URL`, `list_tools` succeeds and contains the expected tool names; one harmless read call
   each (company-db `list_teams`, hr `org_chart`, workstations `list_workstations`).
3. Postgres reachable and seeded (transactions count > 0); MLflow `MLFLOW_TRACKING_URI` reachable if set.
Add the `smoke` command to `deploy/cfo-entrypoint.sh` and a short section in `docs/cluster-setup.md`.

## Context
Settings from `cfo.config` (same env as api/worker). `LLM_VERIFY_TLS` handling as in `cfo/llm.py`. Unit tests use
fakes (httpx MockTransport, InProcessToolBox, fake MCP client); no network in tests.

## Out of scope
Changing deploy YAML beyond the entrypoint command; fixing what the smoke check finds (separate tasks).
