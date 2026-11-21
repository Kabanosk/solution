import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
import { currentSession } from "@/lib/auth";
import { backendError } from "@/lib/revenue-plan-model";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const reply = (body: unknown, status = 200) => Response.json(body, { status, headers: noStore });

export async function POST(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const session = await currentSession();
  if (session?.role !== "owner") return reply({ error: "Only a workspace owner can run the forecast review." }, 403);
  try {
    const upstream = await apiFetch("/v1/forecast/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal: AbortSignal.timeout(10000) });
    const data = await upstream.json().catch(() => null) as Record<string, unknown> | null;
    if (!upstream.ok) { const failed = backendError(upstream.status, data, "The forecast review is temporarily unavailable."); return reply({ error: failed.error }, failed.status); }
    const status = data?.status === "already_scheduled" ? "already_scheduled" : data?.status === "queued" ? "queued" : null;
    if (!status) return reply({ error: "The forecast review returned an unexpected response." }, 502);
    return reply({ status, task_id: typeof data?.task_id === "string" ? data.task_id.slice(0, 80) : null });
  } catch { return reply({ error: "The forecast review is temporarily unavailable." }, 503); }
}
