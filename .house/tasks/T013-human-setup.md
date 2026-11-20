---
id: T013
title: Human: Slack workspace, cluster access, NeMo Guardrails
status: todo
owner: Wojtek
branch: null
depends_on: []
capabilities: [human]
acceptance:
  - id: AC-1
    manual: "Slack workspace exists, app installed from deploy/slack-app-manifest.yaml, tokens in the cluster Secret, team members invited as demo employees"
  - id: AC-2
    manual: "Cluster credentials received; model endpoint URL and name recorded in .env / ConfigMap"
  - id: AC-3
    manual: "NeMo Guardrails proxy in front of vLLM (Wojtek, separate chat; see reports/guardrails-research.md)"
protected: []
---
## Objective
Things only humans do. Owner: Wojtek (Slack in a separate chat, guardrails himself). Not for AI workers.
