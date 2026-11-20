---
id: T012
title: OpenShift deploy YAMLs (Postgres, app, MLflow, model endpoint)
status: todo
owner: null
branch: null
depends_on: ["T001"]
capabilities: []
acceptance:
  - id: AC-1
    run: "for f in deploy/openshift/*.yaml; do python3 -c \"import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))\" \"$f\" || exit 1; done"
    timeout: 900
  - id: AC-2
    run: "test -f docs/deploy.md"
    timeout: 900
  - id: AC-3
    manual: "oc apply -k deploy/openshift on the team cluster brings up Postgres, the app and MLflow; app reaches the model endpoint; judge records evidence"
protected: []
---
## Objective
Plain YAML + kustomize under `deploy/openshift/` (D-008, no Terraform): namespace, Postgres (StatefulSet/PVC or template), seed Job (runs the generator + loader), backend Deployment/Service/Route (API) and agent worker, MLflow, Secret templates (Slack tokens, LLM key), ConfigMap with `LLM_BASE_URL`/`LLM_MODEL` pointing at the existing vLLM Llama 3.2 3B endpoint. `Containerfile` for the backend (UBI Python). `docs/deploy.md` step by step for someone who knows k8s but not OpenShift.

## Context
- Single node, 32 CPU / 128 GB, one L4 24 GB (used by the model). Keep requests small.
- NeMo Guardrails is Wojtek's own work (D-009); only leave the `LLM_BASE_URL` switch.

## Out of scope
Model serving changes, guardrails.
