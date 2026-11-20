---
id: T023
title: OpenShift: API, agent worker, MCP servers, config
status: review
owner: coder
branch: task/T023-deploy-app
depends_on: ["T021"]
capabilities: []
acceptance:
  - id: AC-1
    run: "for f in deploy/openshift/app/*.yaml; do python3 -c \"import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))\" \"$f\" || exit 1; done"
    timeout: 900
protected: []
---
## Objective
Deployments + Services for API (with Route), agent worker and the three MCP servers (HTTP transport, ports 8001-8003), ConfigMap with `LLM_BASE_URL`/`LLM_MODEL` (existing vLLM Llama 3.2 3B endpoint), Secret templates for Slack tokens and LLM key, kustomization. Part of former T012.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
