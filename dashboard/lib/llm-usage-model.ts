export type UsageTotals = {
  requests: number; failedRequests: number; estimatedRequests: number;
  promptTokens: number; completionTokens: number;
  p50Ms: number | null; p95Ms: number | null;
  busyCost: string; amortizedCost: string;
};
export type UsageReport = {
  from: string; to: string; totals: UsageTotals;
  buckets: (UsageTotals & { key: string })[];
  actions: { action: string; actions: number; requests: number; busyCost: string; amortizedCost: string }[];
  comparisons: { model: string; cost: string; asOf: string }[];
  hourlyRate: string; tokenShare: string; shareSource: string; notes: string[];
};
export type ModelSnapshot = {
  model: string; sampledAt: string; stale: boolean;
  running: number | null; waiting: number | null; cacheUsage: string | null;
  promptRate: string | null; generationRate: string | null;
};
export type LlmUsageData = {
  report: UsageReport | null; snapshot: ModelSnapshot | null;
  reportUnavailable: boolean; snapshotUnavailable: boolean; fetchedAt: string; preview: boolean;
};

function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid telemetry");
  return value as Record<string, unknown>;
}
function text(value: unknown): string {
  if (typeof value !== "string") throw new Error("Invalid telemetry text");
  return value;
}
function timestamp(value: unknown): string {
  const result = text(value);
  if (!Number.isFinite(Date.parse(result))) throw new Error("Invalid telemetry timestamp");
  return result;
}
function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("Invalid telemetry count");
  return value;
}
function nullableNumber(value: unknown): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw new Error("Invalid telemetry number");
  return value;
}
function decimal(value: unknown): string {
  const result = text(value);
  if (!/^\d+(\.\d+)?$/.test(result) || !Number.isFinite(Number(result))) throw new Error("Invalid telemetry decimal");
  return result;
}
function cost(value: unknown): string {
  const row = object(value);
  const result = text(row.amount);
  if (row.currency !== "EUR" || !/^\d+\.\d{6}$/.test(result)) throw new Error("Invalid telemetry cost");
  return result;
}
function rows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new Error("Invalid telemetry list");
  return value.map(object);
}
function totals(row: Record<string, unknown>): UsageTotals {
  return {
    requests: count(row.requests), failedRequests: count(row.failed_requests), estimatedRequests: count(row.estimated_requests),
    promptTokens: count(row.prompt_tokens), completionTokens: count(row.completion_tokens),
    p50Ms: nullableNumber(row.latency_p50_ms), p95Ms: nullableNumber(row.latency_p95_ms),
    busyCost: cost(row.cost_busy_time), amortizedCost: cost(row.cost_amortized),
  };
}

export function parseUsageReport(value: unknown): UsageReport {
  const row = object(value);
  if (row.stub !== false || row.currency !== "EUR") throw new Error("Unsupported telemetry report");
  const assumptions = object(row.assumptions);
  if (!Array.isArray(assumptions.notes) || !assumptions.notes.every(note => typeof note === "string")) throw new Error("Invalid telemetry notes");
  return {
    from: timestamp(row.from), to: timestamp(row.to), totals: totals(object(row.total)),
    buckets: rows(row.items).map(bucket => ({ ...totals(bucket), key: text(bucket.key) })),
    actions: rows(row.per_action).map(action => ({ action: text(action.action_type), actions: count(action.actions), requests: count(action.requests), busyCost: cost(action.cost_per_action_busy_time), amortizedCost: cost(action.cost_per_action_amortized) })),
    comparisons: rows(row.hosted_comparison).map(item => ({ model: text(item.model), cost: cost(item.cost), asOf: text(item.as_of) })),
    hourlyRate: decimal(assumptions.hourly_rate_eur), tokenShare: decimal(assumptions.token_share), shareSource: text(assumptions.token_share_source), notes: assumptions.notes as string[],
  };
}

export function parseModelSnapshot(value: unknown): ModelSnapshot {
  const row = object(value);
  if (row.stub !== false || typeof row.stale !== "boolean") throw new Error("Unsupported telemetry snapshot");
  const optionalDecimal = (value: unknown) => value === null ? null : decimal(value);
  const optionalCount = (value: unknown) => value === null ? null : count(value);
  const cacheUsage = optionalDecimal(row.kv_cache_usage);
  if (cacheUsage !== null && Number(cacheUsage) > 1) throw new Error("Invalid cache usage");
  return { model: text(row.model), sampledAt: timestamp(row.sampled_at), stale: row.stale,
    running: optionalCount(row.requests_running), waiting: optionalCount(row.requests_waiting), cacheUsage,
    promptRate: optionalDecimal(row.prompt_tokens_per_second), generationRate: optionalDecimal(row.generation_tokens_per_second) };
}

export function formatUsageCost(amount: string): string {
  if (!/^\d+\.\d{6}$/.test(amount)) throw new Error("Invalid cost");
  const [whole, fraction] = amount.split(".");
  return `€${BigInt(whole).toLocaleString("en-US")}.${fraction}`;
}

export function previewLlmUsage(days: number): LlmUsageData {
  const report: UsageReport = {
    from: new Date(Date.UTC(2026, 10, 21 - days)).toISOString(), to: "2026-11-21T00:00:00.000Z",
    totals: { requests: 48, failedRequests: 2, estimatedRequests: 3, promptTokens: 52400, completionTokens: 8200, p50Ms: 1240, p95Ms: 2860, busyCost: "0.012444", amortizedCost: "1.764000" },
    buckets: [
      { key: "2026-11-19", requests: 20, failedRequests: 1, estimatedRequests: 1, promptTokens: 21000, completionTokens: 3200, p50Ms: 1200, p95Ms: 2700, busyCost: "0.004444", amortizedCost: "0.700000" },
      { key: "2026-11-20", requests: 28, failedRequests: 1, estimatedRequests: 2, promptTokens: 31400, completionTokens: 5000, p50Ms: 1280, p95Ms: 2900, busyCost: "0.008000", amortizedCost: "1.064000" },
    ].filter(bucket => days !== 1 || bucket.key === "2026-11-20"),
    actions: [{ action: "attach_invoice", actions: 12, requests: 28, busyCost: "0.000667", amortizedCost: "0.088667" }, { action: "alert_cost_anomaly", actions: 8, requests: 20, busyCost: "0.000556", amortizedCost: "0.087500" }],
    comparisons: [{ model: "Example hosted model", cost: "0.018400", asOf: "2026-11-20" }],
    hourlyRate: "0.70", tokenShare: "0.35", shareSource: "vllm_metrics", notes: ["Development sample data. Costs and model activity are invented."]
  };
  if (days === 1) report.totals = { ...report.buckets[0] };
  return { report, snapshot: { model: "Sample model", sampledAt: "2026-11-20T12:00:00.000Z", stale: false, running: 1, waiting: 0, cacheUsage: "0.24", promptRate: "145.8", generationRate: "32.6" }, reportUnavailable: false, snapshotUnavailable: false, fetchedAt: "2026-11-20T12:00:05.000Z", preview: true };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export type ChartBucket = UsageTotals & { key: string; label: string };
const emptyTotals = (): UsageTotals => ({ requests: 0, failedRequests: 0, estimatedRequests: 0, promptTokens: 0, completionTokens: 0, p50Ms: null, p95Ms: null, busyCost: "0.000000", amortizedCost: "0.000000" });

/** One bucket per day (or hour) of the report range, zero-filled, so charts show quiet periods too. Keys are UTC. */
export function fillBuckets(report: Pick<UsageReport, "from" | "to" | "buckets">, unit: "day" | "hour"): ChartBucket[] {
  const width = unit === "day" ? 10 : 13; // "YYYY-MM-DD" or "YYYY-MM-DDTHH"
  const keyOf = (value: string) => {
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) && value.length > 10 ? new Date(parsed).toISOString().slice(0, width) : value.slice(0, width);
  };
  const byKey = new Map(report.buckets.map(bucket => [keyOf(bucket.key), bucket]));
  const step = unit === "day" ? 86_400_000 : 3_600_000;
  const start = Date.parse(report.from);
  const end = Date.parse(report.to);
  const out: ChartBucket[] = [];
  if (Number.isFinite(start) && Number.isFinite(end) && end > start && (end - start) / step <= 800) {
    for (let at = Math.floor(start / step) * step; at < end; at += step) {
      const key = new Date(at).toISOString().slice(0, width);
      const date = new Date(at);
      const label = unit === "day" ? `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]}` : `${String(date.getUTCHours()).padStart(2, "0")}:00`;
      const bucket = byKey.get(key);
      byKey.delete(key);
      out.push({ ...(bucket ?? emptyTotals()), key, label });
    }
  }
  // Buckets the range walk did not cover (odd keys) are still shown, at the end.
  for (const [key, bucket] of byKey) out.push({ ...bucket, key, label: key });
  return out;
}
