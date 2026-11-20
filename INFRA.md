# INFRA.md: infrastructure cheat sheet

One page with everything infra: what runs where, the commands we keep typing, env vars, ports, gotchas.

Where else to look, so nothing is written twice:
- [`docs/cluster-setup.md`](docs/cluster-setup.md): getting `oc`, logging in, the base services (`postgres`,
  `files`), port-forward, the DB password, re-applying `deploy/`. Start there if you are new to the cluster.
- [`docs/slack-setup.md`](docs/slack-setup.md): Slack app and tokens. [`docs/api.md`](docs/api.md): API endpoints.
- [`deploy/openshift/app/README.md`](deploy/openshift/app/README.md): app manifests, LLM endpoint, secrets. `docs/deploy.md` (T025, planned).

Keep this file short and current: when you change infra, update the table it touches.

Status legend: ✅ running · 🟡 written, not yet on the cluster · ⏳ planned (task id)

## 1. Map

| What | Local (laptop) | Cluster (the selected project) | Status |
|---|---|---|---|
| Postgres 16 | `docker compose up -d db` | Deployment `postgres`, svc `postgres:5432`, PVC `postgres-data` (5Gi), Secret `postgres` | ✅ |
| Sample data (organizer CSV etc.) | `data/` (git-ignored) | Deployment `files`, svc `http://files:8080` (read-only), PVC `sample-data` | ✅ |
| Backend image | `Containerfile` → `cfo-backend` | ImageStream + BuildConfig `cfo-backend` (`deploy/openshift/app/`) | 🟡 T021/T023 |
| Seed Job (migrate + generate + load) | `cfo migrate/generate/load` | ⏳ | ⏳ T022 |
| API (FastAPI) | `:8080` dev / `:8000` in image | Deployment + Service + Route `api` | 🟡 T023 |
| MCP servers company-db / workstations / hr | `:8001` / `:8002` / `:8003` | Deployments + Services `mcp-*` (internal only) | 🟡 T023 |
| Agent worker (sim + agent + Slack) | `cfo worker` | Deployment `worker` (needs T011 `cfo.run`) | 🟡 T023 |
| LLM (vLLM, Llama 3.2 3B) | n/a | already on RHOAI (L4 24 GB) | ✅ (URL: see §6) |
| NeMo Guardrails proxy | n/a | in front of vLLM, same `LLM_BASE_URL` switch | ⏳ Wojtek |
| MLflow | optional (`MLFLOW_TRACKING_URI`) | ⏳ own Deployment or the RHOAI one | ⏳ T024 |

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

Simulator (December 2025, shared state in Postgres):

```sh
uv run --project backend python -m cfo.sim status
uv run --project backend python -m cfo.sim start --speed 3600 --run   # 1 simulated hour per second, foreground
uv run --project backend python -m cfo.sim pause | step | jump 2025-12-15 | reset
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
| `cfo worker` | `python -m cfo.run` (agent worker, T011) |
| `cfo migrate` / `generate` / `load` | same as the modules in §2 |
| `cfo <anything>` | exec'd as is: `cfo sh`, `cfo python -m cfo.sim status` |

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
| `LLM_BASE_URL` | `http://localhost:8000/v1` | vLLM or the NeMo proxy, see §6 |
| `LLM_MODEL` | `llama-3.2-3b` | the served model name, see §6 |
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
| `LLM_VERIFY_TLS` | `true` | `true`, `false` (self-signed certificate) or a CA bundle path for an https `LLM_BASE_URL`; the Granite service on the cluster is https with a self-signed certificate |
| `VLLM_METRICS_URL` | unset (usage sampler off) | `https://modelcar-granite-4-0-h-tiny-kserve-workload-svc.my-first-model.svc.cluster.local:8000/metrics` (T032) |
| `VLLM_METRICS_VERIFY` | `true` | TLS check of the metrics URL: `true` / `false` / CA bundle path |
| `GPU_HOURLY_COST_EUR`, `NODE_HOURLY_COST_EUR` | `0.70`, `0` | hourly rate of one L4 (AWS g6.xlarge based assumption, dated in `docs/api.md`) and an optional node share |
| `HOSTED_PRICES_JSON`, `USD_TO_EUR`, `LLM_USAGE_RECORD`, `VLLM_METRICS_STALE_SECONDS`, `VLLM_METRICS_BEARER_TOKEN`, `VLLM_METRICS_TIMEOUT` | see `docs/api.md` | optional tuning of the LLM usage and cost estimate |
| `API_PORT` | `8000` | image only |

## 6. Fill in (things only humans know)

| What | Value |
|---|---|
| Console URL | TODO |
| API server URL (`oc login --server`) | TODO |
| vLLM endpoint (`LLM_BASE_URL`) | TODO |
| Served model name (`LLM_MODEL`) | TODO |
| NeMo Guardrails proxy URL | TODO |
| API Route (public URL for the dashboard) | TODO (after T023) |

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
