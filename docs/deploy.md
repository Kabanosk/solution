# Deploying the Autonomous CFO on OpenShift

For people who know Kubernetes but not OpenShift. This page is the order of steps from an empty project to a
working demo, plus how to check, debug and roll back. The detailed procedures live in other files; this page
links to them instead of repeating them:
[`docs/cluster-setup.md`](cluster-setup.md) (login, CSV upload, seed, smoke),
[`deploy/openshift/app/README.md`](../deploy/openshift/app/README.md) (app manifests, LLM endpoint, secrets),
[`deploy/openshift/dashboard/README.md`](../deploy/openshift/dashboard/README.md),
[`deploy/openshift/mlflow/README.md`](../deploy/openshift/mlflow/README.md),
[`docs/slack-setup.md`](slack-setup.md), [`INFRA.md`](../INFRA.md) (cheat sheet, env vars, gotchas).

## OpenShift in Kubernetes terms

| OpenShift | Kubernetes equivalent | What it means here |
|---|---|---|
| `oc` | `kubectl` + extras | every `kubectl` command works as `oc` |
| Project | Namespace | ours is selected with `oc project`; manifests carry no namespace |
| Route | Ingress | edge TLS, hostname under the cluster's apps domain (`oc get route`) |
| BuildConfig + ImageStream | CI job + registry | the backend image is built *in the cluster* from GitHub `main` (`oc start-build cfo-backend`) |
| SecurityContextConstraints | PodSecurity | containers run as a random UID in group 0: no `runAsUser`, files must be group-writable |
| InferenceService (KServe, OpenShift AI) | Deployment + Service for a model | Granite on vLLM, in project `my-first-model`, not part of `deploy/` |

## 1. Log in and pick the project

```sh
oc login --token=<token> --server=<api-url>      # console: your name -> Copy login command
oc project <project>
```
Details: `docs/cluster-setup.md` §1–2.

## 2. Secrets (once per project, never committed: the repo is public)

| Secret | Keys | How |
|---|---|---|
| `postgres` | `POSTGRESQL_USER`, `POSTGRESQL_PASSWORD`, `POSTGRESQL_DATABASE` | create before the first apply; `deploy/postgres.yaml` only reads it: `oc create secret generic postgres --from-literal=POSTGRESQL_USER=cfo --from-literal=POSTGRESQL_PASSWORD="$(openssl rand -hex 16)" --from-literal=POSTGRESQL_DATABASE=cfo` |
| `slack` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_USER_MAP`, `API_WRITE_TOKEN` | `docs/slack-setup.md`; `API_WRITE_TOKEN` = `openssl rand -hex 24` |
| `dashboard-auth` | dashboard Basic login | `deploy/openshift/dashboard/README.md` |

```sh
oc create secret generic slack \
  --from-literal=SLACK_BOT_TOKEN='xoxb-...' --from-literal=SLACK_APP_TOKEN='xapp-...' \
  --from-literal=SLACK_USER_MAP='vincenzo=U...' --from-literal=API_WRITE_TOKEN="$(openssl rand -hex 24)"
```
The api and worker read `slack` as optional: without it they start, Slack is off and the API write endpoints are
open. Change one key later without touching the rest:
`oc patch secret slack --type=merge -p '{"stringData":{"SLACK_BOT_TOKEN":"xoxb-..."}}'`, then restart (step 8).

## 3. The model (Granite 4.0 H Tiny on vLLM)

Served by OpenShift AI as InferenceService `modelcar-granite-4-0-h-tiny` in project `my-first-model` (created in
the console, not in this repo). The app needs these vLLM arguments on it:

```sh
oc patch isvc modelcar-granite-4-0-h-tiny -n my-first-model --type=json -p '[{"op":"replace","path":"/spec/predictor/model/args","value":["--port=8080","--model=/mnt/models","--served-model-name=modelcar-granite-4-0-h-tiny","--enable-auto-tool-choice","--tool-call-parser=hermes","--enable-prefix-caching","--max-model-len=32768"]}]'
```

- `--enable-auto-tool-choice --tool-call-parser=hermes`: native tool calling (the agent and the CEO chat call
  MCP tools). Without them every request with `tools` gets HTTP 400 and the app falls back to JSON in text.
- `--enable-prefix-caching`: repeated system prompts are served from cache (3.5k-token prompt: 0.94 s first,
  0.37 s after). It works in blocks of 512 tokens and is experimental for Granite's Mamba layers.
- `--max-model-len=32768`: required with prefix caching. At the default 131072 tokens vLLM needs 10 GiB of KV
  cache, the L4 has 6.15 GiB left, and the engine does not start. Our prompts are a few thousand tokens.
- `replace` sets the whole list; `add` on an existing list overwrites it as well, so always send every argument.

**One GPU:** after any change the new predictor pod stays `Pending` (`Insufficient nvidia.com/gpu`) while the
old one holds the GPU. Delete the old pod by hand; the model is down for 3–5 minutes:

```sh
oc get pods -n my-first-model                    # old: Running, new: Pending
oc delete pod -n my-first-model <old-pod>
oc logs -n my-first-model -f <new-pod> -c kserve-container   # wait for "Application startup complete"
```
If the new pod crash-loops, `oc logs --previous`, then patch the previous argument list back and delete the
failing pod. Never do this during the demo.

The app reaches the model at
`http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/v1` (ConfigMap `cfo-config`,
`LLM_BASE_URL`; served name `modelcar-granite-4-0-h-tiny` = `LLM_MODEL`). Check:
`oc exec deploy/api -- curl -s <LLM_BASE_URL>/models`.

## 4. Sample data

Upload the organizer bank CSV into the `files` volume before the first seed: `docs/cluster-setup.md` §6.

## 5. Apply everything

```sh
oc diff -k deploy/            # optional: what will change
oc apply -k deploy/           # postgres, files, seed job, api, worker, mcp-*, usage-sampler, mlflow
oc start-build cfo-backend --follow
```
`deploy/kustomization.yaml` renders with the kustomize built into old `kubectl`/`oc` (v4.5) too; check with
`oc kustomize deploy/`. The seed job waits for the first image and then migrates, generates and loads the data
(`docs/cluster-setup.md` §7). The dashboard is a separate kustomization with its own build:
`deploy/openshift/dashboard/README.md`.

## 6. Check

```sh
oc get pods -l app.kubernetes.io/part-of=cfo     # all Running/Completed
oc rsh deploy/api cfo smoke                      # LLM, tool calling, 3 MCP servers, seed, MLflow
oc get route api dashboard
```
`cfo smoke` prints one line per check and exits 1 on any `FAIL`. The last cluster run: 11/11 PASS (2026-11-20).
API docs: `https://<api route>/docs`. MLflow has no authentication: open it with
`oc port-forward svc/mlflow 5000:5000` and http://localhost:5000.

## 7. Day to day: logs and debugging

```sh
oc logs -f deploy/worker                         # agent, simulator, Slack socket
oc logs -f deploy/api
oc logs <pod> --previous                         # the crashed container
oc describe pod <pod>                            # why it does not start (events at the bottom)
oc get events --sort-by=.lastTimestamp | tail -20
oc debug deploy/<name>                           # shell in a copy of a crash-looping pod
```
More in `INFRA.md` §4.

## 8. Updating

| Change | Do |
|---|---|
| Code merged to GitHub `main` | `oc start-build cfo-backend --follow`, then `oc rollout restart deploy/api deploy/worker deploy/mcp-company-db deploy/mcp-hr deploy/mcp-workstations deploy/usage-sampler` |
| Manifests in `deploy/` | `oc apply -k deploy/` |
| ConfigMap `cfo-config` or a Secret | pods do not restart on their own: `oc rollout restart deploy/api deploy/worker` |
| Fresh demo data (also resets the simulator) | `oc delete job cfo-seed && oc apply -k deploy/`; never during a live demo |

## 9. Rollback

```sh
oc rollout undo deploy/<name>                    # previous ReplicaSet (previous image or env)
oc rollout history deploy/<name>
oc get builds                                    # older images: oc tag cfo-backend@<sha256> cfo-backend:latest
```
Model arguments: patch the previous list back (step 3). Data: re-seed (step 8). `postgres`, `files` and
`mcp-workstations` use `strategy: Recreate` (ReadWriteOnce volumes), so their rollout briefly stops the old pod
before the new one starts. `cfo-data` is ReadWriteOnce and shared by api, worker, mcp-workstations and the seed
job: fine on our single node, needs ReadWriteMany on a multi-node cluster.
