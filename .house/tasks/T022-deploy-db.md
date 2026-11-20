---
id: T022
title: OpenShift: Postgres and seed Job
status: todo
owner: null
branch: null
depends_on: ["T021"]
capabilities: []
acceptance:
  - id: AC-1
    run: "for f in deploy/openshift/db/*.yaml; do python3 -c \"import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))\" \"$f\" || exit 1; done"
    timeout: 900
protected: []
---
## Objective
Postgres (StatefulSet + PVC + Service + Secret template) and a seed Job running migrate + generator + loader. The bank CSV is already on the cluster (uploaded by Grzesiu; ask the coordinator for the ConfigMap/PVC name, Q-002). Part of former T012.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
