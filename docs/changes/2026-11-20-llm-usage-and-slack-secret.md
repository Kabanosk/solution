# Change note: LLM usage endpoints and the `slack` Secret (2026-11-20)

Two changes landed on `main` this afternoon:

1. **LLM usage and cost endpoints** for the dashboard (task T032, decision D-019). They report what running the agent on
   our cluster costs, based on real usage of the Granite model served by vLLM.
2. **Slack credentials come from the team Secret `slack`**. The duplicate `cfo-secrets` Secret is gone.

Full reference for every field: [`docs/api.md`](../api.md#get-v1llmusage-and-get-v1llmusagelive).

## 1. LLM usage endpoints

### What the dashboard can show

| Widget | Endpoint | Fields |
|---|---|---|
| "Cost of the agent today / this week" | `GET /v1/llm/usage?group_by=day` | `total.cost_busy_time`, `total.cost_amortized`, `items[]` per day |
| "Cost per decision" (e.g. per reconciled invoice, per prepared credit line) | same call | `per_action[]`: `action_type`, `actions`, `cost_per_action_busy_time`, `cost_per_action_amortized` |
| "Where do tokens go" (invoice extraction vs explanations) | `GET /v1/llm/usage?group_by=purpose` | `items[].key` (purpose), `prompt_tokens`, `completion_tokens` |
| "Own model vs hosted API" | any `/v1/llm/usage` call | `hosted_comparison[]` (same tokens at a hosted model's list price) |
| Latency | any `/v1/llm/usage` call | `latency_p50_ms`, `latency_p95_ms` |
| Live model status tile | `GET /v1/llm/usage/live` | `requests_running`, `requests_waiting`, `kv_cache_usage` (0..1), `prompt_tokens_per_second`, `generation_tokens_per_second`, `stale` |
| Assumptions footnote | any `/v1/llm/usage` call | `assumptions` (rates, sources, dates). Show it: the costs are estimates |

Query parameters of `/v1/llm/usage`: `from` and `to` (ISO 8601, UTC; default: the last 7 days) and
`group_by` = `day` | `hour` | `event_type` | `purpose`.

Costs carry **six decimals** (`{"amount": "0.000250", "currency": "EUR"}`), because one call costs a fraction of a cent.
Everything else follows the usual API conventions: money as a string plus currency, ISO timestamps,
`{"error": {"code", "message"}}`.

### Try it

```bash
API="https://$(oc get route api -o jsonpath='{.spec.host}')"
curl -s "$API/v1/llm/usage/live"
curl -s "$API/v1/llm/usage?group_by=purpose"
curl -s "$API/v1/llm/usage?from=2026-11-21T00:00:00Z&to=2026-11-22T00:00:00Z&group_by=hour"
```

Checked on the cluster on 2026-11-20 at 16:16: `/live` returned a fresh Granite sample, and the first recorded call
(37 prompt and 10 completion tokens, 1.28 s) cost about 0.00025 EUR busy-time or 0.016 EUR amortized. The numbers grow
once the agent processes events, e.g. after `POST /v1/sim:start`.

### How the numbers are made

- **Per call:** every LLM call made through `backend/cfo/llm.py` writes one row to `llm_usage`. The row holds the
  time, model, prompt and completion tokens (from the server's `usage` field), latency, the agent step (`purpose`) and
  the event and decision it belongs to. Recording never breaks a call.
- **Whole model server:** the `usage-sampler` Deployment runs `python -m cfo.usage sample --every 60` and stores
  snapshots of vLLM's Prometheus `/metrics` in `vllm_metrics_samples`. Differences between snapshots give usage for any
  time range. A counter that goes down is treated as a pod restart, so history survives restarts.
- **Cost model.** All rates are assumptions; the response returns each one with its source and date.
  - `cost_busy_time` is GPU busy time multiplied by the hourly rate. The default rate is 0.70 EUR/h, one NVIDIA L4 on
    demand (AWS g6.xlarge, USD 0.8048/h, converted at 0.87 EUR/USD).
  - `cost_amortized` is wall-clock hours multiplied by the hourly rate and by our share of the tokens the server
    processed.
  - `hosted_comparison` prices the same tokens at gpt-5-mini (USD 0.25 / 2.00 per 1M in/out) and gpt-5-nano
    (USD 0.05 / 0.40).

### Configuration (ConfigMap `cfo-config`)

| Variable | Value on the cluster | Notes |
|---|---|---|
| `VLLM_METRICS_URL` | `http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/metrics` | Granite runs as a plain KServe InferenceService over HTTP (D-020). The old `https://…-kserve-workload-svc…:8000` URLs no longer work |
| `VLLM_METRICS_VERIFY` | not needed for `http://` | `false` or a CA path only for a self-signed `https://` endpoint |
| `LLM_VERIFY_TLS` | not needed for `http://` | same, for the LLM client |
| `GPU_HOURLY_COST_EUR`, `NODE_HOURLY_COST_EUR`, `USD_TO_EUR`, `HOSTED_PRICES_JSON` | defaults | change the rates without a code change |
| `LLM_USAGE_RECORD` | `true` | `false` turns per-call recording off |

The original single-organization setup ran one sampler replica. The shared app now runs zero; see the
[current deployment guidance](../../deploy/openshift/app/README.md#fill-in-the-llm-endpoint). Several replicas, or
several model pods behind one service, would make counters look like restarts.

### Files

- `backend/cfo/usage/`: metrics parser, sampler, cost model.
- `backend/cfo/llm.py`: per-call recording; `LLM_VERIFY_TLS` setting.
- `backend/cfo/api/`: the two routes.
- `backend/cfo/db/schema.sql`: tables `llm_usage` and `vllm_metrics_samples`. Run `cfo migrate`, which the seed Job
  already does.
- `deploy/openshift/app/usage-sampler.yaml`: the sampler Deployment; `configmap.yaml` gets the new keys.
- `backend/tests/test_llm_usage.py`: 46 tests. The main fixture is a real `/metrics` capture from the Granite pod
  (`backend/tests/fixtures/vllm_metrics_granite.txt`).

### Known limits

- LLM calls that happen after a CEO approval resumes the agent are not attributed to a decision yet; they still appear
  in the totals.
- Hosted-model prices come from search results, because the vendor pages could not be fetched. Present them as list-price
  assumptions.
- `cost_amortized` includes idle time, so it is much higher than `cost_busy_time` while traffic is low. Show both and
  explain the difference.

## 2. Slack credentials: Secret `slack`

- The team created the Secret `slack` in the selected project with the keys `SLACK_APP_TOKEN`, `SLACK_BOT_TOKEN` and
  `SLACK_USER_MAP` (for example `vincenzo=U…,grazyna=U…`). The values are not in git.
- `deploy/openshift/app/worker.yaml` and `api.yaml` now load it with
  `envFrom: [{secretRef: {name: slack, optional: true}}]`.
- The duplicate Secret `cfo-secrets` was deleted. `deploy/openshift/app/README.md` and the ConfigMap comments explain
  how to recreate `slack` if needed:

  ```bash
  oc create secret generic slack \
    --from-literal=SLACK_BOT_TOKEN='xoxb-…' \
    --from-literal=SLACK_APP_TOKEN='xapp-…' \
    --from-literal=SLACK_USER_MAP='vincenzo=U…,grazyna=U…'
  oc rollout restart deploy/worker deploy/api
  ```

- Result: the worker restarted and opened its Slack Socket Mode session. DMs and the Approve and Reject buttons go
  through the bot.
