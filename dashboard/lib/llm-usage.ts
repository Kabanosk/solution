import "server-only";
import { requireSession } from "./auth";
import { apiFetch } from "./scout-api";
import { parseModelSnapshot, parseUsageReport, previewLlmUsage, type LlmUsageData } from "./llm-usage-model";

async function read(path: string): Promise<unknown> {
  const response = await apiFetch(path, { signal: AbortSignal.timeout(8000) });
  if (!response.ok) throw new Error("Telemetry unavailable");
  return response.json();
}

export async function getLlmUsage(days: number): Promise<LlmUsageData> {
  if (![1, 7, 30].includes(days)) throw new Error("Invalid telemetry range");
  await requireSession();
  const now = new Date();
  const from = new Date(now.getTime() - days * 86400000);
  const params = new URLSearchParams({ from: from.toISOString(), to: now.toISOString(), group_by: days === 1 ? "hour" : "day" });
  const [report, snapshot] = await Promise.allSettled([
    read(`/v1/llm/usage?${params}`).then(parseUsageReport),
    read("/v1/llm/usage/live").then(parseModelSnapshot),
  ]);
  return { report: report.status === "fulfilled" ? report.value : null, snapshot: snapshot.status === "fulfilled" ? snapshot.value : null,
    reportUnavailable: report.status === "rejected", snapshotUnavailable: snapshot.status === "rejected", fetchedAt: now.toISOString(), preview: false };
}
