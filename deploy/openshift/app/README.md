# App manifests (`deploy/openshift/app/`)

Part of `oc apply -k deploy/` (the root kustomization lists this directory). Namespace `cfo-agent-uwur`.

| File | What |
|---|---|
| `imagestream.yaml`, `buildconfig.yaml` | Image `cfo-backend:latest`, built in the cluster from `https://github.com/Kabanosk/solution` (`main`, `Containerfile`) |
| `configmap.yaml` | `cfo-config`: `LLM_BASE_URL`, `LLM_MODEL` (placeholders), MCP URLs, `SIM_SPEED`, `CORS_ORIGINS` |
| `pvc.yaml` | `cfo-data` (1Gi), mounted at `/app/data` by `mcp-workstations` and by the seed Job (T022) |
| `api.yaml` | Deployment + Service + edge TLS Route `api` (`cfo api`, port 8000) |
| `worker.yaml` | Deployment `worker` (`cfo worker` = `python -m cfo.run`: simulator, agent, Slack socket; no Service; mounts `cfo-data` read-only for the December replay) |
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
oc rollout restart deploy/api deploy/worker
```

To go through NeMo Guardrails, point `LLM_BASE_URL` at the proxy instead (D-009).

## Secrets (never commit values; the repo is public)

Slack tokens live in Secret `slack` (keys `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_USER_MAP`), read by the worker
and the api (`optional`, so pods start without it). Created once by the team, never committed:

```sh
oc create secret generic slack \
  --from-literal=SLACK_BOT_TOKEN='xoxb-...' \
  --from-literal=SLACK_APP_TOKEN='xapp-...' \
  --from-literal=SLACK_USER_MAP='vincenzo=U...,grazyna=U...'
oc rollout restart deploy/worker deploy/api
```

The vLLM endpoint needs no API key (LLM_API_KEY defaults to EMPTY). Where the Slack tokens come from: `docs/slack-setup.md`.

## Database

Pods build `DATABASE_URL` from the keys `POSTGRESQL_USER`, `POSTGRESQL_PASSWORD`, `POSTGRESQL_DATABASE` of
Secret `postgres` (`postgresql://$(USER):$(PASSWORD)@postgres:5432/$(DATABASE)`). A password with `@ : / ?`
would break the URL; keep it alphanumeric or percent-encode it.

## Worker and the simulated clock

The worker ticks the December replay and gives every event to the agent. The clock starts paused: start it with
`POST /v1/sim:start` (body `{"speed": 3600}` = one simulated hour per second) or
`oc exec deploy/worker -- cfo python -m cfo.sim start --speed 3600`. Without the Slack tokens the worker logs
the DMs instead of sending them (`oc logs deploy/worker`). Reminders (24 simulated hours after a request), the
daily cost-vs-metric check and the liquidity review (when the replay has finished) run inside the worker;
`COST_CHECK_HOURS`, `LIQUIDITY_CHECK_HOURS`, `REMINDER_AFTER_HOURS` change the intervals.

The api pod runs with `AGENT_IN_API=1`: it builds its own agent on the same Postgres checkpointer, so Approve /
Reject from the dashboard and events emitted by `POST /v1/sim:step` are handled there. It reads Secret `slack`
too (it sends DMs with `SLACK_BOT_TOKEN`; only the worker opens the Slack socket). After changing the secret:
`oc rollout restart deploy/api deploy/worker`.

## Notes

- Images: all five pods use `cfo-backend:latest` through the `image.openshift.io/triggers` annotation.
  A new build restarts them.
- `mcp-workstations` uses `strategy: Recreate` (RWO volume). Its data (`/app/data/generated/workstations`) is
  empty until the seed Job has run.
- Requests are small (single node, the GPU belongs to the model): api 100m/384Mi, worker 100m/256Mi,
  MCP servers 50m/128Mi each.
