# INFRA.md: infrastructure cheat sheet

One page with everything infra: what runs where, the commands we keep typing, env vars, ports, gotchas.

Where else to look, so nothing is written twice:
- [`docs/cluster-setup.md`](docs/cluster-setup.md): getting `oc`, logging in, the base services (`postgres`,
  `files`), port-forward, the DB password, re-applying `deploy/`. Start there if you are new to the cluster.
- [`docs/slack-setup.md`](docs/slack-setup.md): Slack app and tokens. [`docs/api.md`](docs/api.md): API endpoints.
- [`deploy/openshift/app/README.md`](deploy/openshift/app/README.md): app manifests, LLM endpoint, secrets.
- [`docs/deploy.md`](docs/deploy.md): deploy order from an empty project (OpenShift for Kubernetes people), the
  Granite model arguments, updating and rollback.

Keep this file short and current: when you change infra, update the table it touches.

Status legend: ✅ running · 🟡 written, not yet on the cluster · ⏳ planned (task id)

## 1. Map

| What | Local (laptop) | Cluster (the selected project) | Status |
|---|---|---|---|
| Postgres 16 | `docker compose up -d db` | Deployment `postgres`, svc `postgres:5432`, PVC `postgres-data` (5Gi), Secret `postgres` | ✅ |
| Sample data (organizer CSV etc.) | `data/` (git-ignored) | Deployment `files`, svc `http://files:8080` (read-only), PVC `sample-data` | ✅ |
| Backend image | `Containerfile` → `cfo-backend` | ImageStream + BuildConfig `cfo-backend` (`deploy/openshift/app/`), built from GitHub `main` | ✅ |
| Seed Job (Scout seed: company + Scout tables + bank mock + demo evidence) | `cfo seed --reset` | Job `cfo-seed` (`deploy/openshift/db/`) | 🟡 Scout |
| API (FastAPI) | `:8080` dev / `:8000` in image | Deployment + Service + Route `api` | ✅ |
| MCP servers company-db / workstations / hr | `:8001` / `:8002` / `:8003` | Deployments + Services `mcp-*` (internal only) | ✅ |
| Scout worker pool (watchers + job queue + Slack) | `cfo worker` | Deployment `scout-worker` (`scout.worker`) | 🟡 Scout |
| Bank mock (Revolut-shaped API) | `cfo bank-mock` | Deployment + Service `bank-mock:8010` | 🟡 Scout |
| LLM usage sampler | `python -m cfo.usage` | Deployment `usage-sampler` (vLLM `/metrics` → Postgres, T032) | ✅ |
| Dashboard (Next.js) | `dashboard/` | Deployment + Route `dashboard`, own build (`deploy/openshift/dashboard/`), Basic login | ✅ |
| LLM (vLLM, Granite 4.0 H Tiny) | n/a | InferenceService `modelcar-granite-4-0-h-tiny` in project `my-first-model` (L4, one GPU); args in §8 | ✅ |
| NeMo Guardrails (check-only sidecar) | n/a | `NemoGuardrails` CR `scout-guardrails`, svc `scout-guardrails:443` (TLS, bearer token), 3 configs, `deploy/openshift/guardrails/`, [`docs/guardrails.md`](docs/guardrails.md) | ✅ T051; Scout client ⏳ T052 |
| MLflow | optional (`MLFLOW_TRACKING_URI`) | Deployment + svc `mlflow:5000` (`deploy/openshift/mlflow/`); Route has no auth, prefer `oc port-forward svc/mlflow 5000:5000` | ✅ |

Everything on the cluster carries `app.kubernetes.io/part-of=cfo`. Base manifests: `deploy/` (`oc apply -k deploy/`).

Cluster budget: 1 node, 32 CPU / 128 GB, one L4 (taken by the model). Keep requests small. **The cluster is
deleted after the event**, so nothing on it is a backup.

## 2. Local dev

```sh
cp .env.example .env                     # then edit; every var has a default
docker compose up -d db                  # Postgres for the backend, see the port note in §7
uv sync --project backend                # deps into backend/.venv

uv run --project backend python -m cfo.db.migrate            # apply backend/cfo/db/schema.sql (--check = dry run)
uv run --project backend python -m cfo.data.generate         # synthetic company -> data/generated (--source <bank.csv>, --seed 42)
uv run --project backend python -m cfo.data.load --reset     # data/generated -> Postgres

uv run --project backend uvicorn cfo.api.app:app --port 8080 --reload   # API, docs at http://localhost:8080/docs
uv run --project backend python -m cfo.mcp_servers hr --transport streamable-http   # MCP over HTTP (:8003)
```

Scout, locally (Postgres from `.env`; the LLM through `oc port-forward`):

```sh
uv run --project backend python -m scout.seed --reset      # company + Scout tables, shifted statement, demo evidence
uv run --project backend python -m scout.mock_bank         # bank mock on 127.0.0.1:8010
uv run --project backend python -m scout.worker            # worker pool + watchers + Slack Socket Mode
uv run --project backend python -m scout.demo play nowhere # post a live demo payment (files|email|chat_refund|nowhere|overspend_chat)
```

Tests and lint:

```sh
python3 -m unittest discover -s backend/tests -t backend       # or: uv run --project backend pytest
uvx ruff check backend                                         # ruff is not a project dep
```


## 3. Backend image

```sh
docker build -f Containerfile -t cfo-backend .            # UBI 9 Python 3.12, non-root, ~2 GB
docker run --rm -p 8000:8000 --env-file .env cfo-backend  # default command: cfo api
docker run --rm cfo-backend help                          # list of commands
```

The image entrypoint is the `cfo` dispatcher (`deploy/cfo-entrypoint.sh`):

| Command | Does |
|---|---|
| `cfo api` (default) | uvicorn on `0.0.0.0:${API_PORT:-8000}` |
| `cfo mcp <company-db\|workstations\|hr> [port]` | MCP server, streamable HTTP on 8001/8002/8003, endpoint `/mcp` |
| `cfo worker` | `python -m scout.worker` (Scout worker pool) |
| `cfo bank-mock` | `python -m scout.mock_bank` on port 8010 |
| `cfo seed [--reset]` | `python -m scout.seed` (demo company, Scout tables, bank mock) |
| `cfo migrate` / `generate` / `load` | same as the modules in §2 |
| `cfo <anything>` | exec'd as is: `cfo sh`, `cfo python -m scout.demo list` |

The image has no data: `data/generated` and the source CSV come from a volume or from `cfo generate` with
the CSV mounted (`SOURCE_CSV`). Healthcheck: `GET /healthz` → `{"status":"ok"}`.

## 4. Cluster: daily commands

Login, `oc project`, port-forward, the DB password and `oc apply -k deploy/`: see `docs/cluster-setup.md`.
Below is what it does not cover: debugging and day-to-day changes.

```sh
oc get pods -w                                    # watch pods
oc describe pod <pod>                             # why it does not start (events at the bottom)
oc get events --sort-by=.lastTimestamp | tail -20

oc logs deploy/<name> -f                          # follow logs
oc logs <pod> --previous                          # logs of the crashed container
oc rsh deploy/<name>                              # shell inside
oc debug deploy/<name>                            # copy of the pod with a shell, for crash loops

oc diff -k deploy/                                # what `oc apply -k deploy/` would change
oc rollout restart deploy/<name>                  # restart after a Secret/ConfigMap change
oc rollout undo deploy/<name>                     # back to the previous ReplicaSet
oc set env deploy/<name> --list                   # effective env vars

oc rsh deploy/postgres psql -U cfo cfo            # psql without a port-forward
curl -s http://files:8080/                        # sample data, from any pod in the namespace
```

In-cluster `DATABASE_URL`: `postgresql://cfo:<password>@postgres:5432/cfo`.

Secrets (never commit the values, the repo is public):

```sh
oc create secret generic <name> --from-env-file=<file>        # or --from-literal=KEY=value
oc get secret <name> -o jsonpath='{.data.KEY}' | base64 -d
oc set env deploy/<name> --from=secret/<name>                 # inject all keys as env vars
```

## 5. Env vars

All read by `backend/cfo/config.py` from the environment or `.env` (template: `.env.example`).

| Var | Default | Cluster value |
|---|---|---|
| `DATABASE_URL` | `postgresql://cfo:cfo@localhost:5433/cfo` | `postgresql://cfo:<pw>@postgres:5432/cfo` |
| `LLM_BASE_URL` | `http://localhost:8000/v1` | `http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/v1`  |
| `LLM_MODEL` | `llama-3.2-3b` | `modelcar-granite-4-0-h-tiny` |
| `LLM_API_KEY` | `EMPTY` | Secret if the endpoint needs a token |
| `SLACK_BOT_TOKEN` / `SLACK_APP_TOKEN` | empty | Secret (`xoxb-…` / `xapp-…`), see `docs/slack-setup.md` |
| `SLACK_USER_MAP` | empty | `employee_id=SLACK_ID,…` |
| `WORKSTATIONS_ROOT` | `data/generated/workstations` | path on the data volume |
| `SIM_SPEED` | `1.0` | `3600` for demos |
| `CORS_ORIGINS` | `*` | the dashboard origin |
| `SOURCE_CSV` | auto-detect | bank CSV for `cfo generate` |
| `FORECAST_MODEL_PATH` | `models/liquidity_xgb.json` | path to the saved XGBoost model |
| `MLFLOW_TRACKING_URI` | unset (MLflow off) | MLflow service URL (T024). Read from the process environment only (not from `.env`), like every other `MLFLOW_*` variable |
| `MLFLOW_AGENT_EXPERIMENT` | `cfo-agent` | experiment that receives one trace per agent event when `MLFLOW_TRACKING_URI` is set (`cfo/observability.py`, T027) |
| `FORECAST_MODEL_URI` | empty (local model file) | `models:/liquidity-forecast@champion`: the API serves this registry version when `MLFLOW_TRACKING_URI` is set too; the alias is re-read every 60 s, any MLflow error falls back to `FORECAST_MODEL_PATH` |
| `MLFLOW_TRACKING_AUTH`, `MLFLOW_WORKSPACE`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_EXPERIMENT_NAME` | unset | standard MLflow client variables, the code sets none of them. RHOAI MLflow: `MLFLOW_TRACKING_AUTH=kubernetes-namespaced` (+ `MLFLOW_WORKSPACE=<project>` from another namespace). Suggested for the API pod: `MLFLOW_HTTP_REQUEST_MAX_RETRIES=1`, `MLFLOW_HTTP_REQUEST_TIMEOUT=10` |
| `LLM_VERIFY_TLS` | `true` | `true`, `false` (self-signed certificate) or a CA bundle path for an https `LLM_BASE_URL`; the cluster uses the plain-HTTP predictor service (D-020), so the CA path set in `cfo-config` is unused there |
| `VLLM_METRICS_URL` | unset (usage sampler off) | `http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/metrics` (T032) |
| `VLLM_METRICS_VERIFY` | `true` | TLS check of the metrics URL: `true` / `false` / CA bundle path |
| `GPU_HOURLY_COST_EUR`, `NODE_HOURLY_COST_EUR` | `0.70`, `0` | hourly rate of one L4 (AWS g6.xlarge based assumption, dated in `docs/api.md`) and an optional node share |
| `HOSTED_PRICES_JSON`, `USD_TO_EUR`, `LLM_USAGE_RECORD`, `VLLM_METRICS_STALE_SECONDS`, `VLLM_METRICS_BEARER_TOKEN`, `VLLM_METRICS_TIMEOUT` | see `docs/api.md` | optional tuning of the LLM usage and cost estimate |
| `API_PORT` | `8000` | image only |

## 6. Where things are

| What | Value |
|---|---|
| Console URL | `oc whoami --show-console` |
| API server URL (`oc login --server`) | `oc whoami --show-server` |
| vLLM endpoint (`LLM_BASE_URL`) | `http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/v1` (in-cluster only) |
| Served model name (`LLM_MODEL`) | `modelcar-granite-4-0-h-tiny` |
| NeMo Guardrails URL | `https://scout-guardrails.<project>.svc.cluster.local` (CA: service-ca.crt; token: Secret `scout-guardrails-client`, key `token`) |
| API / dashboard Routes | `oc get route api dashboard` (hosts are not written here: the repo is public) |

## 7. Gotchas

- **Postgres port locally:** the backend defaults to `localhost:5433` (compose maps `5433:5432`), while the
  cluster and the port-forward from `docs/cluster-setup.md` use `5432`. Pointing the backend at the cluster DB
  means `DATABASE_URL=...@localhost:5432/cfo` in `.env`.
- **Two images, two files:** `Containerfile` = backend (`cfo-backend`); `Dockerfile` = the tiny sample-data file
  server (`compose up files`). An OpenShift Docker build reads `Dockerfile` by default, so a BuildConfig for
  the backend must set `dockerfilePath: Containerfile`.
- **No public Route for `postgres` or `files`** (organizer data), see `docs/cluster-setup.md`.
- **Arbitrary UID:** OpenShift runs containers as a random UID in group 0. Anything the app writes must be
  group-0 writable (the `Containerfile` already does this for `/app`). Do not hardcode `runAsUser`.
- **Red Hat images:** `registry.redhat.io/rhel9/postgresql-16` uses `POSTGRESQL_USER/PASSWORD/DATABASE` and data at
  `/var/lib/pgsql/data`, not the Docker Hub `POSTGRES_*` / `/var/lib/postgresql/data`.
- **Two schemas side by side:** `db/init.sql` (raw bank CSV → `bank_transactions_raw` + view `bank_transactions`)
  and `backend/cfo/db/schema.sql` (`transactions`, `invoices`, … via `cfo migrate`). No name clash; the backend uses
  its own tables.
- **Secret/ConfigMap changes do not restart pods:** `oc rollout restart deploy/<name>`.
- **Single node, RWO volumes:** `strategy: Recreate` for anything with a PVC, otherwise the new pod hangs on the
  volume.

## 8. Granite on vLLM (InferenceService args)

The InferenceService lives in project `my-first-model` and is not in `deploy/`; its vLLM arguments exist only on
the cluster, so they are recorded here. Current list (2026-11-20):

```
--port=8080 --model=/mnt/models --served-model-name=modelcar-granite-4-0-h-tiny
--enable-auto-tool-choice --tool-call-parser=hermes --enable-prefix-caching --max-model-len=32768
```

- Tool calling needs `--enable-auto-tool-choice --tool-call-parser=hermes`; without them `cfo smoke` fails
  `llm.tool_calling` with HTTP 400 and the app runs on the JSON-in-text fallback.
- Prefix caching on this hybrid Mamba model needs `--max-model-len=32768`: at the default 131072 tokens the engine
  wants 10 GiB of KV cache, 6.15 GiB is free, and vLLM does not start.
- A JSON patch with `add` or `replace` on `/spec/predictor/model/args` replaces the whole list: always send all
  arguments. The command, the one-GPU pod swap and the rollback are in `docs/deploy.md` §3.
- Llama 3.2 3B (`llama-32-3b-instruct`) is still listed as an InferenceService, stopped (`serving.kserve.io/stop: "true"`, no pod, no GPU); unused.
