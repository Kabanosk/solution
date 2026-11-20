---
id: T005
title: LangGraph agent: event loop, reconciliation, escalation, approvals
status: review
owner: coder
branch: task/T005-agent-graph
depends_on: ["T003", "T004"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_agent.py"
    timeout: 900
protected: []
---
## Objective
`cfo/agent/graph.py`: LangGraph agent that handles one Event at a time. Flows from `$HOUSE_DIR/demo-flow.md` steps 2–3 and 5–6: missing invoice → search workstation → LLM extracts lines → match amount → store / ask / mismatch notice; escalation (reminder after X h, manager, vacation → up to CEO); approval requests via LangGraph interrupt, resumed by an approve/reject call; every step writes a Decision with PolicyLevel and reason. Sub-agent for investigations (main agent spawns it on its own; D-005).

## Context
- Channel is an interface `Notifier` (`send_dm(employee, text, actions)`) — Slack implements it in T008; tests use a fake.
- All LLM calls through `cfo.llm`; tests use FakeLLM with scripted tool calls.

## Out of scope
Slack transport, forecasting model, HTTP API.
