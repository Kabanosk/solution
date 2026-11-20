---
id: T047
title: Add the LLM dashboard page
status: todo
owner: null
branch: task/T047-llm-page
depends_on: []
capabilities: [frontend, api]
acceptance:
  - id: AC-1
    run: "npm --prefix dashboard test"
    timeout: 120
  - id: AC-2
    run: "npm --prefix dashboard run typecheck"
    timeout: 120
  - id: AC-3
    manual: "The left navigation includes LLM; its page shows model usage and costs, handles missing data, and works on small screens in both themes."
protected: []
---

## Objective

Implement an LLM page in the dashboard with a left navigation entry, using the existing CFO model usage/cost endpoints. Match the concise Linear-inspired Agent jobs style and existing shadcn/ui theme tokens. Show clearly labelled development fixtures only when the existing development-preview guard is enabled.

## Context

Requested explicitly by the user for a subagent. Not started: the user then asked to save all work and stop before disconnecting the internet. Delegate when work resumes. The configured external HOUSE_DIR does not exist on this machine; use the repository's .house copy. Current integration branch: feature/agent-jobs-logs.

Read docs/api.md (LLM usage endpoints), backend/cfo/api/auth.py, dashboard/lib/development-preview.ts and dashboard/components/dashboard.tsx. Keep backend credentials on the server. Provide loading, empty, unavailable and retry states. Use an isolated task worktree; coordinate shared navigation changes with the integrating agent.

## Return

A short summary, changed file paths, validation evidence, and the task branch/commit for integration. Do not publish or deploy.
