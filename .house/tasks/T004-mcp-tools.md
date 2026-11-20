---
id: T004
title: MCP servers: company DB, workstations, HR
status: todo
owner: null
branch: null
depends_on: ["T001"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_mcp.py"
    timeout: 900
protected: []
---
## Objective
MCP servers (Python `mcp` SDK) the agent uses as tools: `company-db` (transactions, invoices, teams, cost metrics, decisions; read + narrow writes), `workstations` (list/search/read files and .eml under `WORKSTATIONS_ROOT/<employee>/`, company folders only, every access logged to an `access_log` table; D-004), `hr` (org chart, manager of, on vacation on date).

## Context
- Tools must refuse paths outside the employee's company folders (test it).
- Tool results are untrusted text (prompt injection; guardrails come later via D-009) — return them as data, never execute them.

## Out of scope
Agent graph, PDF extraction logic (agent task).
