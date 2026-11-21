# App manifests (`deploy/openshift/app/`)

Part of `oc apply -k deploy/` (the root kustomization lists this directory). Uses the selected OpenShift project.

**Organization auth:** first follow [the migration and owner-enrollment guide](../../../docs/scout-auth.md). Deploy matching API/dashboard versions only after a tested backup. Workers require an explicit enrolled organization; legacy connectors require a separate explicit opt-in. Unscoped MCP HTTP deployments remain at zero replicas; the scoped in-process tools are retained.

| File | What |
|---|---|
| `imagestream.yaml`, `buildconfig.yaml` | Image `cfo-backend:latest`, built in the cluster from `https://github.com/Kabanosk/solution` (`main`, `Containerfile`) |
| `configmap.yaml` | `cfo-config`: `LLM_BASE_URL`, `LLM_MODEL` (placeholders), MCP URLs, `BANK_API_URL`, `SCOUT_WORKERS`, `CORS_ORIGINS` |
| `pvc.yaml` | `cfo-data` (1Gi), mounted at `/app/data` by `api`, `scout-worker`, `mcp-workstations` (read-only) and by the seed Job (T022, writes) |
| `api.yaml` | Deployment + Service + edge TLS Route `api` (`cfo api`, port 8000) |
| `scout-worker.yaml` | Deployment `scout-worker` (`cfo worker` = `python -m scout.worker`: watchers, job queue, Slack socket; no Service; mounts `cfo-data` read-only for workstations and the demo mailbox) |
| `bank-mock.yaml` | Deployment + Service `bank-mock` :8010 (`cfo bank-mock` = `python -m scout.mock_bank`, internal only) |
| `mcp.yaml` | `mcp-company-db` :8001, `mcp-workstations` :8002, `mcp-hr` :8003 (Deployment + Service each, internal only) |

## First deploy

```sh
oc apply -k deploy/
oc start-build cfo-backend --follow        # first image; Deployments roll out when it lands
oc get route api -o jsonpath='{.spec.host}'  # public API host, https://<host>/docs
```

## Fill in the LLM endpoint

`LLM_BASE_URL` and `LLM_MODEL` in `configmap.yaml` are placeholders (`CHANGE-ME-...`). Find the vLLM
service in the RHOAI console (model serving, internal endpoint) or `oc get inferenceservice,svc`, then
`curl <url>/models` for the served name. Edit `configmap.yaml`, re-apply, restart:

```sh
oc apply -k deploy/
oc rollout restart deploy/api deploy/scout-worker
```

To go through NeMo Guardrails, point `LLM_BASE_URL` at the proxy instead (D-009).

The `usage-sampler` Deployment has zero replicas in the shared app. vLLM `/metrics` reports
model-server totals, not organization usage, and the sampler requires explicitly authorized legacy
configuration. Do not grant legacy connector access to make this pod start. Tenant-scoped per-request
usage recording does not depend on the sampler. `/v1/llm/usage/live` stops receiving fresh samples and
returns `503 metrics_unavailable` if the organization has no stored sample. Without a sampled
server-token delta, the amortized cost estimate assumes 100% of server traffic.

For an existing shared installation, merging the manifest does not scale the live Deployment down.
An operator can reconcile just this workload with the intended state in the selected project:

```sh
oc scale deployment/usage-sampler --replicas=0
oc get deployment/usage-sampler
```

Verify the Deployment reports `0/0` ready replicas.

## Secrets (never commit values; the repo is public)

Slack app transport token lives in Secret `slack` (`SLACK_APP_TOKEN`, scope `connections:write`). Tenant bot tokens are sealed per organization during OAuth. Legacy demo bot credentials (`SLACK_BOT_TOKEN`, `SLACK_USER_MAP`) are optional and never used as a fallback for new organizations; the worker reads the app-level token
and the api (`optional`, so pods start without it). Created once by the team, never committed:

```sh
oc create secret generic slack \
  --from-literal=SLACK_BOT_TOKEN='xoxb-...' \
  --from-literal=SLACK_APP_TOKEN='xapp-...' \
  --from-literal=SLACK_USER_MAP='vincenzo=U...,grazyna=U...'
oc rollout restart deploy/scout-worker deploy/api
```

### API access

All data routes, docs, streams and mutations require a valid organization-bound opaque member session plus `X-Scout-Organization`. The API independently revalidates membership. A shared `API_WRITE_TOKEN` no longer grants access. Only process health at `/healthz` is public. Do not distribute bearer sessions or log credentials. See the organization-auth guide for deployment checks and data-preserving rollback.

The vLLM endpoint needs no API key (LLM_API_KEY defaults to EMPTY). Where the Slack tokens come from: `docs/slack-setup.md`.

## Database

Pods build `DATABASE_URL` from the keys `POSTGRESQL_USER`, `POSTGRESQL_PASSWORD`, `POSTGRESQL_DATABASE` of
Secret `postgres` (`postgresql://$(USER):$(PASSWORD)@postgres:5432/$(DATABASE)`). A password with `@ : / ?`
would break the URL; keep it alphanumeric or percent-encode it.

## Scout worker

`scout-worker` polls the bank mock (`BANK_API_URL`) and the mailbox, runs the job queue with `SCOUT_WORKERS` threads
and holds the Slack socket. Without the Slack tokens it logs the DMs instead of sending them
(`oc logs deploy/scout-worker`). The API only enqueues jobs (approve/reject); it runs no agent code.

## Notes

- Images: all pods use `cfo-backend:latest` through the `image.openshift.io/triggers` annotation.
  A new build restarts them.
- `mcp-workstations` uses `strategy: Recreate` (RWO volume). Its data (`/app/data/generated/workstations`) is
  empty until the seed Job has run.
- Requests are small (single node, the GPU belongs to the model): api 100m/384Mi, scout-worker 100m/256Mi, bank-mock 50m/128Mi,
  MCP servers 50m/128Mi each.
