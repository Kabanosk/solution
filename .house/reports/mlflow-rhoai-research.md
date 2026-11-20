# MLflow / RHOAI 3.5 research (collabothon-2026)

Scope: RHOAI 3.5 self-managed, single node. Primary sources used: the `opendatahub-io` GitHub repos (operator, mlflow-operator, mlflow fork, dashboard, notebooks), the `opendatahub-io/architecture-context` folder `rhoai-3.5-ea.1` (EA1, not GA), and the upstream `mlflow/mlflow` source and docs. **docs.redhat.com returned HTTP 403 to every fetch** (3.5 install page, 3.5 TP/DP release notes). Anything taken only from search snippets is marked [snippet]. Anything not verified is [U].

## Answers first

1. **MLflow is a managed DSC component in the 3.5 line, but Tech Preview status for 3.5 GA is unverified.** Set `spec.components.mlflowoperator.managementState: Managed` on the `DataScienceCluster`. Create one cluster-scoped `MLflow` CR (`mlflow.opendatahub.io/v1`) named `mlflow`. Storage is a SQL backend (sqlite on a PVC is allowed for dev, Postgres for real use) plus an artifact destination. Auth is Kubernetes bearer tokens (SelfSubjectAccessReview), not a gateway login. The MLflow version is inconsistent across sources (3.10.1 / 3.7.0 / 3.17), so check `/version` on the live server. Registry and GenAI tracing are both present.
2. **Self-hosting is a fallback, not needed if the operator works.** Upstream `mlflow server` flags are below. Workspaces require a SQL backend. Arbitrary-UID notes are [U].
3. **Workbench:** Use the `Notebook` CR (`kubeflow.org/v1`) with the labels and annotations the dashboard writes. The RHOAI datascience imagestream is `s2i-generic-data-science-notebook`, tag `3.7` on current main. Project = namespace with label `opendatahub.io/dashboard: "true"`. The dashboard adds annotation `opendatahub.io/mlflow-instance: mlflow` when the MLflow component is Managed.
4. **Pipelines (DSPA):** the only required field is `objectStorage` (S3-compatible). The DB defaults to a deployed MariaDB. It needs Argo plus several controller pods. I could not verify resource cost. For one scheduled retrain on one node, a CronJob running the notebook image is far cheaper.
5. **Tracing:** `mlflow.langchain.autolog()` covers LangGraph (it runs on the LangChain tracer). `mlflow.openai.autolog()` covers the `openai` client. Both together give one combined trace. Tracing exists from MLflow 2.14.0. Custom code-only scorers use `@scorer` and `mlflow.genai.evaluate(data=..., predict_fn=..., scorers=[...])`. The `Safety()` built-in in the doc example is an LLM judge, so omit it.

---

## 1. MLflow in RHOAI 3.5

**Component existence and status**
- ODH operator API: `DataScienceCluster.spec.components.mlflowoperator` (type `DSCMLflowOperator`, `MLflowOperatorComponentName = "mlflowoperator"`, singleton instance `default-mlflowoperator`). Source: https://github.com/opendatahub-io/opendatahub-operator/blob/incubation/api/components/v1alpha1/mlflowoperator_types.go and `api/datasciencecluster/v2/datasciencecluster_types.go` (field `MLflowOperator componentApi.DSCMLflowOperator json:"mlflowoperator"`). The `main` branch was also checked and shows the same field.
- The 3.5 EA1 component map lists `mlflow-operator` with `ref: rhoai-3.5-ea.1`, `tier: optional_platform`, `capability: mlflowoperator`, and `mlflow` as a payload component. Source: https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/component-map.json
- Search snippet [snippet]: the 3.5 install guide shows `oc patch datasciencecluster default-dsc` with `mlflowoperator` `managementState: Managed`, and says the operator creates a cluster-scoped MLflow resource named `mlflow`. Source: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-mlflow_mlflow (403 on fetch; snippet only).
- Search snippet [snippet]: 3.4 EA2 release notes say "Red Hat OpenShift AI includes a Technology Preview of MLflow". The 3.5 TP/DP pages were not readable, so **3.5 GA support level is [U]**. Source: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/release_notes/technology-preview-features_relnotes (403).
- Known 3.5 issues from search snippets [snippet]: RHOAIENG-66859 (evaluation jobs with an MLflow experiment never finish; workaround: do not set an experiment). RHOAIENG-60292 (migrations after upgrade; workaround: scale MLflow to 1 replica during upgrade). Source: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/release_notes/known-issues_relnotes (403; snippet only).

**CR to create**
- `apiVersion: mlflow.opendatahub.io/v1`, `kind: MLflow`, `metadata.name: mlflow`. The CRD is cluster-scoped and rejects any other name. Source: `api/v1/mlflow_types.go` on `main`, lines 684 and 689 (`+kubebuilder:resource:scope=Cluster`, `self.metadata.name == 'mlflow'`): https://github.com/opendatahub-io/mlflow-operator/blob/main/api/v1/mlflow_types.go
- Conflict: the 3.4 install snippet shows the CR in namespace `redhat-ods-applications`. The code says cluster scope. Trust the code: **do not set a namespace**. [U] for 3.5 specifically.
- Minimal dev example (upstream sample, `main`): https://github.com/opendatahub-io/mlflow-operator/blob/main/config/samples/mlflow_v1_mlflow_minimal.yaml
  - `backendStoreUri: "sqlite:////mlflow/mlflow.db"`, `artifactsDestination: "file:///mlflow/artifacts"`, `serveArtifacts: true`, `storage` PVC 5Gi (RWO), `image: quay.io/opendatahub/mlflow:odh-stable`.
- Production-ish example (upstream sample, `main`): https://github.com/opendatahub-io/mlflow-operator/blob/main/config/samples/mlflow_v1_mlflow_remote_storage.yaml
  - `backendStoreUriFrom` (Secret key with a `postgresql://` URI), `artifactsDestination: s3://...`, `serveArtifacts: true`, `envFrom` for AWS creds.
- Validation rules that matter (`api/v1/mlflow_types.go`, lines 67–92):
  - `backendStoreUri` or `backendStoreUriFrom` is required. Only sqlite, postgresql or mysql schemes are accepted.
  - A `file://` artifact destination needs `serveArtifacts` or `artifactsServer` enabled, plus `storage`.
  - `artifactsServer` cannot use SQLite.
  - `MLFLOW_SERVER_ENABLE_JOB_EXECUTION` and `MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE` cannot be set in `env`.
  - `storage` and its `accessModes` are immutable once set.

**Storage requirements (summary)**
- Metadata store: SQL only. Postgres or MySQL in production; SQLite on a PVC is allowed for dev. Source: the same file, lines 67–76.
- Artifacts: S3-compatible (`s3://`) or a PVC via `file://` with `serveArtifacts: true`. Source: `config/samples/*` above, and `ARCHITECTURE.md` "Storage and Availability Decisions" (https://github.com/opendatahub-io/mlflow-operator/blob/main/ARCHITECTURE.md).
- Workspaces (the namespace-to-MLflow mapping) require a SQL backend. File-based stores are not supported with workspaces. Source: https://github.com/opendatahub-io/mlflow/blob/master/docs/docs/self-hosting/workspaces/getting-started.mdx

**Auth model**
- The server runs `--app-name=kubernetes-auth`, `--enable-workspaces`, `--workspace-store-uri=kubernetes://`, and sets `MLFLOW_K8S_AUTH_AUTHORIZATION_MODE=self_subject_access_review`. Each request is authorized with the caller's own token. Source: `ARCHITECTURE.md` (mlflow-operator, "Rendering Strategy"), and README "Authentication and Security": https://github.com/opendatahub-io/mlflow-operator/blob/main/README.md
- TLS terminates in the pod on 8443 using a `mlflow-tls` Secret auto-created by the OpenShift service-ca. Source: README "Authentication and Security" and `ARCHITECTURE.md` "TLS Model".
- Namespace RBAC: with `ENABLE_NAMESPACE_RBAC`, the operator creates `odh-group-mlflow-view` and `odh-group-mlflow-edit` RoleBindings in namespaces labelled for it. Source: README "Operator RBAC Privileges". [U] whether RHOAI sets this label automatically.

**Tracking URI and how a workbench or another namespace connects**
- Status fields: `status.url` (external, via the data-science-gateway at `/mlflow`), `status.address.url` (in-cluster HTTPS URL of the Service). Source: README "Create MLflow instances".
  - Check with `oc get mlflow mlflow -o jsonpath='{.status.address.url}'`. The exact in-cluster hostname form is [U] until you look at the live status.
- Client config (from the MLflow fork docs, `opendatahub-io/mlflow`): https://github.com/opendatahub-io/mlflow/blob/master/docs/docs/self-hosting/security/kubernetes.mdx
  - `MLFLOW_TRACKING_AUTH=kubernetes-namespaced` adds `Authorization: Bearer <SA token>` plus `X-MLFLOW-WORKSPACE: <namespace>`. `kubernetes` adds only the bearer token.
  - Credentials come from `/var/run/secrets/kubernetes.io/serviceaccount/{token,namespace}`, falling back to kubeconfig.
  - `MLFLOW_TRACKING_TOKEN` overrides the token. `MLFLOW_WORKSPACE` overrides the namespace.
- So a pod in another namespace sets `MLFLOW_WORKSPACE=<its target project>` and uses a ServiceAccount that has RBAC in that project. Workbenches in the project get the namespace automatically.
- The TLS trust chain for the in-cluster HTTPS endpoint (service-ca CA) is [U]. Probably `REQUESTS_CA_BUNDLE` or `MLFLOW_TRACKING_INSECURE_TLS` for the pilot, but I did not verify it.
- REST header: `X-MLFLOW-WORKSPACE` (same getting-started doc).

**MLflow version: conflicting, verify live**
- Architecture context, RHOAI 3.5-ea.1 mlflow.md: "Version: 3.10.1+rhaiv.3 (rhoai-3.5-ea.1 branch)". Source: https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/mlflow.md
- Chart `appVersion: "3.7.0"` in mlflow-operator `charts/mlflow/Chart.yaml` (https://github.com/opendatahub-io/mlflow-operator/blob/main/charts/mlflow/Chart.yaml).
- Operator `ARCHITECTURE.md` refers to "the supported MLflow 3.17 runtime". The RHOAI workbench image manifest lists `MLflow 3.17`. Source: https://github.com/opendatahub-io/notebooks/blob/main/manifests/rhoai/base/jupyter-datascience-notebook-imagestream.yaml
- Action: `curl -k https://<status.address.url>/version`. The mlflow.md doc says `/version` returns the version string.

**Model registry and tracing**
- Model registry: `registryStoreUri` in the CR, defaulting to the backend store. Source: `api/v1/mlflow_types.go` line 203 and `mlflow.md` ("model registry").
- GenAI tracing: the operator's ARCHITECTURE mentions OTLP span ingest at `/mlflow/v1/traces` and `X-MLFLOW-WORKSPACE` for OTLP clients (https://github.com/opendatahub-io/mlflow-operator/blob/main/ARCHITECTURE.md; getting-started doc above). The fork's `mlflow.md` lists "MLflow Tracing" as a component.

## 2. Self-hosted fallback (only if 1 fails)

- Latest upstream release: `v3.18.0`, published 2026-11-04. Source: `gh api repos/mlflow/mlflow/releases/latest`. The image `ghcr.io/mlflow/mlflow:<tag>` was not checked [U].
- Upstream `mlflow server` options (from `mlflow/cli/__init__.py` at tag `v3.18.0`): https://github.com/mlflow/mlflow/blob/v3.18.0/mlflow/cli/__init__.py
  - `--backend-store-uri` (env `MLFLOW_BACKEND_STORE_URI`). Help text: SQLAlchemy URIs such as `postgresql://...`. Default is local SQLite.
  - `--default-artifact-root` (env `MLFLOW_DEFAULT_ARTIFACT_ROOT`). Help text: "For tracking server backends that rely on SQL, this option is required in order to store artifacts."
  - `--serve-artifacts` (the `cli_args.SERVE_ARTIFACTS` flag) and `--artifacts-destination` (env `MLFLOW_ARTIFACTS_DESTINATION`). The destination is the base for upload/download.
  - `--enable-workspaces/--disable-workspaces` (default off).
  - `--host`, `--port`.
- Minimal command (from the flags above, not run):
  `mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri postgresql://user:pass@db:5432/mlflow --artifacts-destination /mlflow/artifacts --serve-artifacts --default-artifact-root mlflow-artifacts:/`
  - `--default-artifact-root mlflow-artifacts:/` is a proxy alias chosen by me, not verified against the 3.18 docs [U].
- Arbitrary-UID gotchas on OpenShift (random UID in restricted SCC, writable PVC with `fsGroup`, sqlite file in a non-writable image dir) are general knowledge, **not verified from a source** [U]. Prefer Postgres to avoid SQLite file writes.

## 3. Workbench (Notebook CR)

- Dashboard-built Notebook shape (source: `backend/src/utils/notebookUtils.ts` in odh-dashboard, https://github.com/opendatahub-io/odh-dashboard/blob/main/backend/src/utils/notebookUtils.ts, lines ~200–260):
  - `apiVersion: kubeflow.org/v1`, `kind: Notebook`
  - labels: `opendatahub.io/dashboard: "true"`, `opendatahub.io/odh-managed: "true"`, `app: <name>`
  - annotations: `notebooks.opendatahub.io/last-image-selection: <imagestream-name>:<tag>`, `notebooks.opendatahub.io/last-size-selection`, `opendatahub.io/username`, `opendatahub.io/hardware-profile-name` (+ namespace), `notebooks.opendatahub.io/inject-auth: "true"` (line 412), and `opendatahub.io/mlflow-instance: mlflow` when the DSC `mlflowoperator` is Managed (lines 140–143, 237).
  - container image: `<dockerImageRepo>:<tag>`; env `NOTEBOOK_ARGS` with `--ServerApp.port=8888 --ServerApp.base_url=/notebook/<ns>/<name>`.
  - [U] the controller's effect of `opendatahub.io/mlflow-instance`. I did not find the code that consumes it, so do not rely on it for auth or env injection.
- Namespace = Data Science Project: label `opendatahub.io/dashboard: "true"` on the namespace. Source: dashboard BFF `distributions/core-bff/bff/internal/models/namespace.go` line 23 (`IsAiProject: ns.Labels["opendatahub.io/dashboard"] == "true"`): https://github.com/opendatahub-io/odh-dashboard/blob/main/distributions/core-bff/bff/internal/models/namespace.go
- Image stream names:
  - RHOAI datascience: `s2i-generic-data-science-notebook`, display "Jupyter | Data Science | CPU | Python 3.12", recommended tag `3.7`. Source: https://github.com/opendatahub-io/notebooks/blob/main/manifests/rhoai/base/jupyter-datascience-notebook-imagestream.yaml. On that main-branch copy, the stream is also annotated `opendatahub.io/deprecated: "true"`, and its bundled MLflow client is 3.17. Verify the 3.5 stream on the cluster: `oc get imagestream -n redhat-ods-applications -l opendatahub.io/notebook-image=true`.
  - Image-stream annotation format: https://github.com/opendatahub-io/notebooks/blob/main/docs/workbench-imagestreams.md
- Env vars and connections: use a ConfigMap/Secret with the dashboard's env-var mechanism (`envVars.configMaps` / `envVars.secrets`, rendered into `valueFrom` refs; `backend/src/utils/notebookUtils.ts` ~line 190–220). Setting `MLFLOW_TRACKING_AUTH=kubernetes-namespaced` and `MLFLOW_WORKSPACE` on the Notebook is standard env, but I did not verify whether the platform injects them [U].

## 4. Data Science Pipelines (DSPA)

- Required in the DSPA CRD schema (rhoai-3.5-ea.1 contracts, `kubeflow/datasciencepipelinesapplication.v1.json`): top-level `objectStorage` is required. Source: https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/contracts/schemas/kubeflow/datasciencepipelinesapplication.v1.json
  - `mariaDB.deploy`, `mariaDB.pvcSize`, `storageClassName`, `mariaDB.image` default to a deployed MariaDB. An external DB requires host, port, username, passwordSecret, pipelineDBName.
  - Object storage child fields not fully extracted [U]. Expect an S3-compatible endpoint, bucket, and credentials Secret.
- Architecture (3.5-ea.1 `data-science-pipelines.md`): Argo Workflows, API server, persistence agent, scheduled-workflow controller, UI, MLMD, plus SeaweedFS/MinIO/S3 and MySQL/PostgreSQL. The RHOAI overlay adds SeaweedFS. Source: https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/data-science-pipelines.md
- Resource cost: [U]. I did not extract the per-pod requests.
- Assessment: for a scheduled retrain on one node, a Kubernetes `CronJob` running the workbench image (with a `ServiceAccount` for MLflow) is much cheaper and fits the one-node budget. Pipelines add Argo, a DB and an object store, and only pay off with multi-step DAGs.

## 5. MLflow tracing for LangGraph + openai, and genai.evaluate

- LangGraph: `mlflow.langchain.autolog()` (LangGraph is part of the LangChain integration). `run_tracer_inline=True` for async `ainvoke`. `autolog(disable=True)` to turn off. Source: https://mlflow.org/docs/latest/genai/tracing/integrations/listing/langgraph/ (fetched; quoted snippets above).
  - Thread/session ID recorded from MLflow 3.6 (same page).
- OpenAI: `mlflow.openai.autolog()`. Source: https://mlflow.org/docs/latest/genai/tracing/integrations/listing/openai/ (fetched).
  - Async OpenAI SDK from MLflow 2.21.0; Responses API from 2.22.0 (same page).
  - The page also says the Decisions API needs `openai>=3.26.0`. I could not confirm that version against PyPI [U].
- Both together: one combined trace (3.0.1 integrations page, via search snippet [snippet]: https://mlflow.org/docs/3.0.1/tracing/integrations/).
- Minimum version: tracing from MLflow 2.14.0 (search snippet [snippet], https://mlflow.org/docs/2.14.0/llms/tracing/index.html). For a 3.x server, use the workbench MLflow client (3.17 in the image) so client and server match.
- Custom code scorers, no LLM judge: https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/code-examples/ (fetched).
  - `from mlflow.genai.scorers import scorer`, `from mlflow.entities import Feedback`
  - `@scorer` decorator on a plain function taking `outputs`, `expectations`, optionally `inputs` and `trace`.
  - `mlflow.genai.evaluate(data=[{"inputs": ..., "expectations": {...}}], predict_fn=app, scorers=[my_scorer])`
  - Return `bool`/`float`/`str`, or `Feedback` for a rationale. Trace-based checks (span search) are deterministic.
  - The docs example uses `Safety()` as a second scorer. That is an LLM judge, so leave it out.
  - Search snippet [snippet]: `evaluate()` needs at least one scorer. Source: https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/

## Open points

- [U] 3.5 GA/TP status of MLflow (docs 403).
- [U] MLflow server version actually deployed (3.10.1 vs 3.7.0 vs 3.17). Check `/version`.
- [U] Exact in-cluster tracking URI and CA-trust env for workbenches.
- [U] Whether RHOAI auto-injects `MLFLOW_TRACKING_*` env or RoleBindings into workbenches.
- [U] DSPA object-storage child fields and per-pod resource cost.
- [U] `ghcr.io/mlflow/mlflow` tag existence (not checked; `quay.io/opendatahub/mlflow:odh-stable` is the operator default).
- [U] OpenShift arbitrary-UID specifics (no source fetched).

## Source list

- https://github.com/opendatahub-io/opendatahub-operator/blob/incubation/api/components/v1alpha1/mlflowoperator_types.go
- https://github.com/opendatahub-io/opendatahub-operator/blob/main/api/datasciencecluster/v2/datasciencecluster_types.go
- https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/component-map.json
- https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/mlflow.md
- https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/mlflow-operator.md
- https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/data-science-pipelines.md
- https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-3.5-ea.1/contracts/schemas/kubeflow/datasciencepipelinesapplication.v1.json
- https://github.com/opendatahub-io/mlflow-operator (README.md, ARCHITECTURE.md, api/v1/mlflow_types.go, charts/mlflow/Chart.yaml, config/samples/*)
- https://github.com/opendatahub-io/mlflow/blob/master/docs/docs/self-hosting/workspaces/getting-started.mdx
- https://github.com/opendatahub-io/mlflow/blob/master/docs/docs/self-hosting/security/kubernetes.mdx
- https://github.com/opendatahub-io/odh-dashboard/blob/main/backend/src/utils/notebookUtils.ts
- https://github.com/opendatahub-io/odh-dashboard/blob/main/distributions/core-bff/bff/internal/models/namespace.go
- https://github.com/opendatahub-io/notebooks/blob/main/manifests/rhoai/base/jupyter-datascience-notebook-imagestream.yaml
- https://github.com/opendatahub-io/notebooks/blob/main/docs/workbench-imagestreams.md
- https://github.com/mlflow/mlflow/blob/v3.18.0/mlflow/cli/__init__.py
- https://mlflow.org/docs/latest/genai/tracing/integrations/listing/langgraph/
- https://mlflow.org/docs/latest/genai/tracing/integrations/listing/openai/
- https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/code-examples/
- Snippets only: https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/working_with_mlflow/installing-mlflow_mlflow ; https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.5/html/release_notes/known-issues_relnotes (both 403 on fetch)
