import "server-only";
import { isDevelopmentPreview } from "./development-preview";
import { parseModelSnapshot, parseUsageReport, previewLlmUsage, type LlmUsageData } from "./llm-usage-model";

async function read(path: string): Promise<unknown> {
  const base = process.env.CFO_API_URL ?? "http://api:8000";
  const token = process.env.CFO_API_TOKEN;
  const response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
    cache: "no-store", signal: AbortSignal.timeout(8000),
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    redirect: "error",
  });
  if (!response.ok) throw new Error("Telemetry unavailable");
  return response.json();
}

export async function getLlmUsage(days: number): Promise<LlmUsageData> {
  if (![1, 7, 30].includes(days)) throw new Error("Invalid telemetry range");
  if (isDevelopmentPreview()) return previewLlmUsage(days);
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
