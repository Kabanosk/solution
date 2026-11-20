---
id: T008
title: Slack bot (Socket Mode): DMs, CEO chat, approve/reject
status: done
owner: coder
branch: task/T008-slack-channel
depends_on: ["T001"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_slack.py"
    timeout: 900
  - id: AC-2
    run: "test -f deploy/slack-app-manifest.yaml"
    timeout: 900
protected: []
---
## Objective
`cfo/channels/slack.py` implementing the `Notifier` interface with slack_bolt in Socket Mode (no public URL): DMs with Block Kit buttons (Approve/Reject) that call back into the agent, CEO free-text chat routed to the agent, every message labelled with its level (AUTO/NOTIFY/APPROVAL/SIMULATED). `deploy/slack-app-manifest.yaml` + `docs/slack-setup.md` (which tokens go where) for the human task T013.

## Context
- Mapping employee ↔ Slack user id from HR data (`slack_id` column) or env.
- Tests: fake Slack client, no network.

## Out of scope
Signal, opencode (later; D-007).
