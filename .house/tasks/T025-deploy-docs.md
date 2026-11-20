---
id: T025
title: Deploy guide for k8s people new to OpenShift
status: todo
owner: null
branch: null
depends_on: ["T022", "T023", "T024"]
capabilities: []
acceptance:
  - id: AC-1
    run: "test -s docs/deploy.md && grep -q \"oc apply -k\" docs/deploy.md"
    timeout: 900
  - id: AC-2
    manual: "oc apply -k deploy/openshift on the team cluster brings up DB, app, MLflow; app reaches the model; judge records evidence"
protected: []
---
## Objective
`docs/deploy.md`: login, project, secrets, `oc apply -k`, seed, check, logs, rollback. Part of former T012.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
