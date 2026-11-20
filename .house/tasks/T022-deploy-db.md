---
id: T022
title: OpenShift: Postgres and seed Job
status: done
owner: coder
branch: task/T022-deploy-db
depends_on: ["T021"]
capabilities: []
acceptance:
  - id: AC-1
    run: "for f in deploy/openshift/db/*.yaml; do python3 -c \"import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))\" \"$f\" || exit 1; done"
    timeout: 900
protected: []
---
## Objective
Postgres itself is already deployed by Grzesiu (PR #2: `deploy/postgres.yaml`, Secret `postgres`, service `postgres:5432`, db `cfo`, namespace the selected project; read `docs/cluster-setup.md` and `INFRA.md`). This task is only the seed: a Job in `deploy/openshift/db/` using our backend image that downloads the bank CSV from the in-cluster file server `http://files:8080/` (PVC `sample-data`), then runs `cfo migrate`, `cfo generate`, `cfo load --reset`, with DB credentials from Secret `postgres`. Reference it from `deploy/kustomization.yaml` without breaking Grzesiu's resources. Part of former T012.

## Context
Decisions in `$HOUSE_DIR/DECISIONS.md`, golden path `$HOUSE_DIR/demo-flow.md`, merged code under `backend/cfo/`, reports of dependencies in `$HOUSE_DIR/reports/`.

## Out of scope
Anything another task owns.
