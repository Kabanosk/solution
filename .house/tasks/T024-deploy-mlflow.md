---
id: T024
title: OpenShift: MLflow tracking server
status: review
owner: coder
branch: task/T024-deploy-mlflow
depends_on: []
capabilities: []
acceptance:
  - id: AC-1
    run: "for f in deploy/openshift/mlflow/*.yaml; do python3 -c \"import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))\" \"$f\" || exit 1; done"
    timeout: 900
protected: []
---
## Objective
MLflow tracking server (Deployment + PVC + Service + Route) unless RHOAI 3.5 already provides one; small resources. Part of former T012.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
