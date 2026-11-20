import { test } from "node:test";
import assert from "node:assert/strict";
import { formatUsageCost, parseModelSnapshot, parseUsageReport, previewLlmUsage } from "../lib/llm-usage-model.ts";

function report() {
  const bucket = { requests: 4, failed_requests: 1, estimated_requests: 2, prompt_tokens: 1000, completion_tokens: 200,
    latency_p50_ms: 1200, latency_p95_ms: 2300, cost_busy_time: { amount: "0.000123", currency: "EUR" }, cost_amortized: { amount: "0.100000", currency: "EUR" } };
  return { stub: false, currency: "EUR", from: "2026-11-20T00:00:00Z", to: "2026-11-21T00:00:00Z", total: bucket,
    items: [{ ...bucket, key: "2026-11-20" }], per_action: [{ action_type: "attach_invoice", actions: 2, requests: 4,
      cost_per_action_busy_time: { amount: "0.000061", currency: "EUR" }, cost_per_action_amortized: { amount: "0.050000", currency: "EUR" } }],
    hosted_comparison: [{ model: "comparison-model", cost: { amount: "0.001234", currency: "EUR" }, as_of: "2026-11-20" }],
    assumptions: { hourly_rate_eur: "0.70", token_share: "0.50", token_share_source: "vllm_metrics", notes: ["Tokens are estimated for 2 calls"] } };
}

test("usage report preserves exact costs, failures and estimated token counts", () => {
  const data = parseUsageReport(report());
  assert.equal(data.totals.requests, 4);
  assert.equal(data.totals.failedRequests, 1);
  assert.equal(data.totals.estimatedRequests, 2);
  assert.equal(data.totals.promptTokens + data.totals.completionTokens, 1200);
  assert.equal(data.totals.busyCost, "0.000123");
  assert.equal(data.actions[0].busyCost, "0.000061");
  assert.equal(data.comparisons[0].cost, "0.001234");
  assert.equal(formatUsageCost("12345678901234567890.000001"), "€12,345,678,901,234,567,890.000001");
});

test("malformed or sample backend reports fail instead of producing invented values", () => {
  assert.throws(() => parseUsageReport({ ...report(), stub: true }));
  assert.throws(() => parseUsageReport({ ...report(), total: { ...report().total, requests: undefined } }));
  assert.throws(() => parseUsageReport({ ...report(), total: { ...report().total, prompt_tokens: -1 } }));
  assert.throws(() => parseUsageReport({ ...report(), total: { ...report().total, cost_busy_time: { amount: "0.12", currency: "EUR" } } }));
  assert.throws(() => parseUsageReport({ ...report(), from: "not a date" }));
});

test("no-call reports keep null latency and empty breakdowns", () => {
  const empty = report();
  const data = parseUsageReport({ ...empty, total: { ...empty.total, requests: 0, failed_requests: 0, estimated_requests: 0,
    prompt_tokens: 0, completion_tokens: 0, latency_p50_ms: null, latency_p95_ms: null }, items: [], per_action: [], hosted_comparison: [] });
  assert.equal(data.totals.p50Ms, null);
  assert.equal(data.totals.requests, 0);
  assert.deepEqual(data.buckets, []);
});

test("model snapshot retains unavailable gauges and staleness", () => {
  const raw = { stub: false, model: "model-name", sampled_at: "2026-11-20T12:00:00Z", stale: true,
    requests_running: null, requests_waiting: 0, kv_cache_usage: null, prompt_tokens_per_second: null, generation_tokens_per_second: "42.5" };
  const data = parseModelSnapshot(raw);
  assert.equal(data.running, null);
  assert.equal(data.waiting, 0);
  assert.equal(data.cacheUsage, null);
  assert.equal(data.stale, true);
  assert.throws(() => parseModelSnapshot({ ...raw, kv_cache_usage: "1.1" }));
  assert.throws(() => parseModelSnapshot({ ...raw, requests_running: undefined }));
});

test("development fixtures are explicitly labelled and their range totals match buckets", () => {
  for (const days of [1, 7, 30]) {
    const data = previewLlmUsage(days);
    assert.equal(data.preview, true);
    assert.equal(data.report?.totals.requests, data.report?.buckets.reduce((sum, row) => sum + row.requests, 0));
    assert.equal(data.reportUnavailable, false);
  }
});

test("usage buckets are zero-filled across the range", async () => {
  const { fillBuckets } = await import("../lib/llm-usage-model.ts");
  const base = { requests: 5, failedRequests: 1, estimatedRequests: 0, promptTokens: 10, completionTokens: 2, p50Ms: 1, p95Ms: 2, busyCost: "0.000001", amortizedCost: "0.000001" };
  const days = fillBuckets({ from: "2026-11-14T09:00:00Z", to: "2026-11-21T09:00:00Z", buckets: [{ ...base, key: "2026-11-20" }] }, "day");
  assert.equal(days.length, 8);
  assert.equal(days.find(bucket => bucket.key === "2026-11-20")?.requests, 5);
  assert.equal(days[0].requests, 0);
  assert.equal(days.at(-2)?.label, "20 Nov");
  const hours = fillBuckets({ from: "2026-11-20T10:00:00Z", to: "2026-11-21T10:00:00Z", buckets: [{ ...base, key: "2026-11-20T12:00:00+00:00" }] }, "hour");
  assert.equal(hours.length, 24);
  assert.equal(hours.find(bucket => bucket.label === "12:00")?.requests, 5);
});
