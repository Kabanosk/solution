---
id: T052
title: "Human (Wojtek): route the app through guardrails and show it in the demo"
status: doing
owner: Wojtek
branch: null
depends_on: ["T051"]
capabilities: [human]
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests"
    timeout: 1500
  - id: AC-2
    manual: "The deployed app sends the traffic chosen in T050 through guardrails; golden path still works end to end"
  - id: AC-3
    manual: "At least one guardrails demo scenario recorded for the pitch (what the user does, what is blocked, where the jury sees it)"
protected: []
---
> **HUMAN TASK — claimed by Wojtek.** Not for AI workers (coder, coder-codex, night runs): do not pick it up,
> do not implement it, do not propose solutions or hints in briefs, reviews or chats unless Wojtek asks.
> Status stays `doing` so `house night` never schedules it (night ignores `capabilities: [human]`).
> Wojtek closes it himself: `house check collabothon-2026 T05x --manual AC-n --evidence <file>`.

## Objective
Put guardrails in the real request path of the deployed app and make it visible to the jury, without breaking
the golden path (demo-flow.md) or the existing tests.

## Context
D-009 (one `LLM_BASE_URL` switch), T041 (no-tools fallback in `cfo/llm.py`), T027 (tracing). If code changes are
needed, Wojtek makes them on his own branch `task/T052-guardrails-wiring` and asks the coordinator to run
`house merge`.

## Out of scope
Changes to the approval policy.
