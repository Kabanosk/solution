---
id: T032
title: LLM usage and cost estimation from vLLM metrics (API)
status: review
owner: coder
branch: task/T032-llm-usage-cost
depends_on: ["T001", "T005", "T018"]
capabilities: []
acceptance:
  - id: AC-1
    run: "uv run --project backend pytest -q backend/tests/test_llm_usage.py"
    timeout: 900
  - id: AC-2
    run: "uv run --project backend python -m cfo.api.export_openapi docs/openapi.json && grep -q \"/v1/llm/usage\" docs/openapi.json && grep -q \"/v1/llm/usage/live\" docs/openapi.json"
    timeout: 900
protected: []
---
## Objective
Estimate and expose what running our agent on the cluster costs, from real usage of the vLLM model server.

1. **Per-call usage (attribution):** `cfo/llm.py` records every LLM call in a new `llm_usage` table: timestamp, model, prompt/completion tokens (from the OpenAI-compatible `usage` field; estimate with a tokenizer-free heuristic if missing and flag it), latency, and context (event id, event type, graph node / purpose, decision id when known). Recording must never break an LLM call (log and continue). `FakeLLM` records too so tests and the local demo have data.
2. **Cluster usage (vLLM Prometheus metrics):** `cfo/usage/vllm_metrics.py` parses the vLLM `/metrics` text format (`vllm:prompt_tokens_total`, `vllm:generation_tokens_total`, `vllm:request_success_total`, `vllm:e2e_request_latency_seconds` histogram, `vllm:num_requests_running`, `vllm:num_requests_waiting`, KV-cache usage gauge under either `vllm:gpu_cache_usage_perc` or `vllm:kv_cache_usage_perc`; label `model_name`). A sampler (`python -m cfo.usage sample --every 60`, also callable from the worker) stores snapshots in `vllm_metrics_samples`; deltas between samples give usage over any range and survive pod restarts (counter reset handling). URL from `VLLM_METRICS_URL` (unset = sampler disabled, endpoints still work on `llm_usage`).
3. **Cost model (configurable, explained in the response):** `GPU_HOURLY_COST_EUR` (default: an L4 on-demand cloud equivalent, document the source and date), optional `NODE_HOURLY_COST_EUR` share; two estimates: *busy-time* (sum of request latency × hourly rate) and *amortized* (wall-clock hours × rate ÷ share of tokens that were ours); plus a *what it would cost on a hosted API* comparison from a small configurable price table (per 1M input/output tokens, clearly labelled as list-price assumptions with date).
4. **API:**
   - `GET /v1/llm/usage?from&to&group_by=day|hour|event_type|purpose` — requests, prompt/completion tokens, p50/p95 latency, estimated cost (both methods) per bucket and total, cost per agent action (e.g. per invoice reconciled, per approval prepared), `assumptions` block with the rates used.
   - `GET /v1/llm/usage/live` — latest vLLM snapshot: running/waiting requests, KV-cache usage, tokens/s over the last sample interval, model name, sample time; 503 `metrics_unavailable` when no sampler data.
   - Same API conventions (money string + currency, ISO timestamps, error model), docs/api.md sections with curl + example responses, regenerated docs/openapi.json.

## Context
Decisions D-008, D-009, D-016; reports T001 (llm.py, schema), T005 (graph nodes calling the LLM), T018-T020 (API conventions); INFRA.md §6 (vLLM endpoint still TODO: everything must be configurable and testable with a fixture `/metrics` text). Another session owns MLflow tracing (T027) which also wraps the openai client: keep recording in `cfo/llm.py` independent of MLflow.

## Out of scope
Dashboard UI; MLflow tracing; Prometheus/Thanos integration of the cluster monitoring stack (can be a later variant).
