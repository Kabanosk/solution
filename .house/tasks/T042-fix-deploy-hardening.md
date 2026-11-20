---
id: T042
title: "Review fixes: deploy runtime gaps (PVC mounts, mlflow extra, local ports, setup guide)"
status: done
owner: coder
branch: task/T042-fix-deploy-hardening
depends_on: ["T011"]
capabilities: []
acceptance:
  - id: AC-1
    run: "kubectl kustomize deploy/ >/dev/null"
    timeout: 300
  - id: AC-2
    run: "grep -q 'extra mlflow' Containerfile"
  - id: AC-3
    run: "docker compose -f docker-compose.yml config | grep -q 'host_ip: 127.0.0.1'"
  - id: AC-4
    run: "uv run --project backend pytest -q backend/tests/test_deploy_manifests.py"
    timeout: 300
protected: []
---
## Objective
Fix the deployment findings of the Codex review (`$HOUSE_DIR/reports/review-fixes.md` items 3, 8, 18, 19, 25):
1. (3) Every pod that reads `data/generated` (API with the simulator routes / in-API agent, worker) mounts PVC
   `cfo-data` at `/app/data` (read-only where it only reads). Add `backend/tests/test_deploy_manifests.py` that
   renders `kubectl kustomize deploy/` and asserts the mounts and that the worker command is a module that exists
   in `backend/cfo` (no `cfo.run` missing again).
2. (8) `Containerfile`: both `uv sync` calls install the `mlflow` extra (`--extra mlflow`), so tracing (T027) and the
   registry (T026) log on the cluster.
3. (19) `docker-compose.yml`: Postgres and the file server bind to `127.0.0.1` only.
4. (18) `docs/cluster-setup.md`: add the step that uploads the bank CSV to the `sample-data` PVC before the seed
   job, with a check command; the seed job's deadline does not count the image-build wait (move the wait to an
   initContainer or raise `activeDeadlineSeconds` and document it).

## Context
- Starts after T011 is merged (it edits api.yaml / worker.yaml). Source reviews: `$HOUSE_DIR/reports/review-T010.md`,
  `review-T015.md`, `review-T020.md`, `review-T022.md`, `review-T023.md`, `review-PR2.md`. Confirm each at HEAD.
- `oc` may not be logged in; the kustomize render and the test are the checks.

## Out of scope
Python application code, LLM endpoint values (human, T013/T029).
