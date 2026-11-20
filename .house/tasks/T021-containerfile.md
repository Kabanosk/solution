---
id: T021
title: Container image for the backend
status: review
owner: coder
branch: task/T021-containerfile
depends_on: ["T001"]
capabilities: []
acceptance:
  - id: AC-1
    run: "docker build -q -f Containerfile -t cfo-backend:test . >/dev/null"
    timeout: 900
protected: []
---
## Objective
`Containerfile` (UBI Python 3.12, uv sync, non-root, OpenShift arbitrary UID friendly) with entrypoints for API, agent worker and the three MCP servers. Part of former T012.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
