---
id: T051
title: "Human (Wojtek): write and deploy the guardrails"
status: doing
owner: Wojtek
branch: null
depends_on: ["T050"]
capabilities: [human]
acceptance:
  - id: AC-1
    manual: "Guardrails configuration and deployment files are in the repo; applying them on the cluster gives a running guardrails service"
  - id: AC-2
    manual: "On the cluster: an unsafe request is blocked and a normal finance question passes (commands + output in reports/T051.md)"
protected: []
---
> **HUMAN TASK — claimed by Wojtek.** Not for AI workers (coder, coder-codex, night runs): do not pick it up,
> do not implement it, do not propose solutions or hints in briefs, reviews or chats unless Wojtek asks.
> Status stays `doing` so `house night` never schedules it (night ignores `capabilities: [human]`).
> Wojtek closes it himself: `house check collabothon-2026 T05x --manual AC-n --evidence <file>`.

## Objective
Write the rails chosen in T050 and run them on the cluster. Wojtek decides which rails, their policies and
prompts, and where the files live.

## Context
Threats in this product (from the review and the challenge): untrusted text in invoices, mail and files read by
the agent; people writing to the bot on Slack; the agent's own answers reaching people. Payment approval stays
in LangGraph (D-005).

## Out of scope
Wiring the app (T052).
