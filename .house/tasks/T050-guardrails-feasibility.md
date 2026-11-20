---
id: T050
title: "Human (Wojtek): NeMo Guardrails feasibility and model choice"
status: doing
owner: Wojtek
branch: null
depends_on: []
capabilities: [human]
acceptance:
  - id: AC-1
    manual: "reports/T050.md answers every question in the Objective with evidence (commands + output)"
  - id: AC-2
    manual: "Decision logged by Wojtek as a D-### in DECISIONS.md: go / no-go, deployment path, model(s) per rail"
protected: []
---
> **HUMAN TASK — claimed by Wojtek.** Not for AI workers (coder, coder-codex, night runs): do not pick it up,
> do not implement it, do not propose solutions or hints in briefs, reviews or chats unless Wojtek asks.
> Status stays `doing` so `house night` never schedules it (night ignores `capabilities: [human]`).
> Wojtek closes it himself: `house check collabothon-2026 T05x --manual AC-n --evidence <file>`.

## Objective
Find out whether NeMo Guardrails can be used in this project on our cluster, and with which model(s).
Questions to answer:
1. Can NeMo Guardrails run on our OpenShift AI cluster, and in which form?
2. Which model(s) should the rails use: the same one the agent uses, or another one, and for which kinds of rails?
3. What does it cost on our single-node cluster (GPU, CPU, memory, latency per turn), and does it fit next to
   what already runs there?
4. Which of our traffic should go through guardrails (agent tool loop, CEO chat, Slack, tool results), and does
   tool calling still work through it?
5. Go / no-go for the demo, with the fallback if no-go.

## Context
- Background material written earlier (read it or not, your call): `.house/reports/guardrails-research.md`,
  `.house/guardrails-tutorial.md`. D-009: guardrails are Wojtek's; the app switches only `LLM_BASE_URL`.
- Cluster: project `cfo-agent-uwur`, LLM in `my-first-model` (D-017).

## Out of scope
Writing rails (T051), wiring the app (T052).
