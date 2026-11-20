---
id: T001
title: Backend skeleton: config, domain types, DB schema, LLM client
status: done
owner: coder
branch: task/T001-contract-skeleton
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_contract.py"
    timeout: 900
  - id: AC-2
    run: "test -f .env.example && grep -q \"^LLM_MODEL=\" .env.example && grep -q \"^LLM_BASE_URL=\" .env.example"
    timeout: 900
  - id: AC-3
    run: "uv run --project backend python -m cfo.db.migrate --check"
    timeout: 900
protected: []
---
## Objective
A Python package `cfo` in `backend/` (uv, `pyproject.toml`, pytest) with the interfaces every later task builds on, plus `docker-compose.yml` (service `db`: Postgres 16 on host port 5433, user/pass/db `cfo`).

## Context
- Decisions: D-002..D-010 in `$HOUSE_DIR/DECISIONS.md`; golden path `$HOUSE_DIR/demo-flow.md`.
- `cfo/config.py`: settings from env / `.env` (pydantic-settings): `DATABASE_URL`, `LLM_BASE_URL`, `LLM_MODEL` (default `llama-3.2-3b`), `LLM_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `WORKSTATIONS_ROOT`, `SIM_SPEED`. `.env.example` documents each one.
- `cfo/llm.py`: model-agnostic chat client over an OpenAI-compatible endpoint (vLLM, or NeMo Guardrails proxy at the same URL; D-009), with tool-calling support and a `FakeLLM` for tests. Nothing else may call an LLM directly.
- `cfo/domain.py`: typed models: Transaction, Invoice (+ lines), Employee (manager_id, vacations), Team, CostMetric, Event, Decision, ApprovalRequest, Rule, PolicyLevel enum `AUTO | NOTIFY | APPROVAL` and an `executed_simulated` flag.
- `cfo/db/schema.sql` + `cfo/db/migrate.py` (`--check` exits 0 when the schema applies cleanly to `DATABASE_URL`), small repository functions.
- `tests/test_contract.py`: config defaults, domain round-trip, FakeLLM tool call.

## Out of scope
Data generation, agent logic, Slack, API routes.
