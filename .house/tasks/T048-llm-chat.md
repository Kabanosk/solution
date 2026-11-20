---
id: T048
title: Replace the LLM metrics page with a Linear-inspired chat
status: done
owner: codex
branch: feature/agent-jobs-logs
depends_on: [T047]
capabilities: [frontend, api]
acceptance:
  - id: AC-1
    run: "npm --prefix dashboard test"
    timeout: 120
  - id: AC-2
    run: "npm --prefix dashboard run typecheck"
    timeout: 120
  - id: AC-3
    manual: "Actual Granite streaming replies retain conversation context; model selection, stop/retry/new chat, desktop/mobile and both themes work. Evidence: reports/T048.md."
protected: []
---

## Objective

Implement the user's corrected LLM page scope: interactive chat, styled after Linear's compact agent interface, with only modelcar-granite-4-0-h-tiny in the model dropdown. Use the provided OpenShift model endpoint through a server-side authenticated proxy. Update configuration and docs.

## Result

Completed and verified against the actual OpenShift model, including a two-turn conversation. Detailed evidence is in reports/T048.md. The existing telemetry API is retained; the LLM page itself is chat. No model deployment or database migration was performed.
