# MLflow tracking server (T024)

Self-hosted MLflow `v3.10.1` in namespace `cfo-agent-uwur`: one pod, SQLite backend store and proxied artifacts on
PVC `mlflow-data` (2Gi), Service `mlflow:5000`, edge-TLS Route `mlflow`. No extra setup (no Postgres database to create).

- In-cluster tracking URI: `http://mlflow:5000` (`MLFLOW_TRACKING_URI` in ConfigMap `cfo-config`; pods pick it up after
  `oc rollout restart deploy/api deploy/worker`).
- UI: `oc get route mlflow -o jsonpath='{.spec.host}'`. No authentication: anyone with the Route URL can read and write.
- Artifacts go through the server (`--serve-artifacts`, `mlflow-artifacts:/`), so clients need no access to the PVC.
- If the Route gets a custom host, add it to `--allowed-hosts` in `mlflow.yaml`, otherwise MLflow answers 403 "Invalid Host header".
- Using the RHOAI-managed MLflow instead: see the report T024. Drop `openshift/mlflow` from `deploy/kustomization.yaml` and point
  `MLFLOW_TRACKING_URI` at it.
