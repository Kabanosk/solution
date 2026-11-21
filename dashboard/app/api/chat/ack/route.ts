import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
export const dynamic = "force-dynamic";

// POST {taskId, stepIds} -> ${CFO_API_URL}/v1/chat/followups/ack: the open chat showed these followup steps.
export async function POST(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const body = await request.json().catch(() => null) as { taskId?: unknown; stepIds?: unknown } | null;
  const ids = body?.stepIds;
  if (typeof body?.taskId !== "string" || !/^SC-\d{1,12}$/.test(body.taskId) || !Array.isArray(ids) || !ids.length || ids.length > 50
    || !ids.every(id => Number.isSafeInteger(id) && id > 0)) {
    return Response.json({ error: "Invalid acknowledgement" }, { status: 400, headers: noStore });
  }
  try {
    const response = await apiFetch("/v1/chat/followups/ack", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task_id: body.taskId, step_ids: ids }),
    });
    return Response.json(await response.json().catch(() => ({})), { status: response.ok ? 200 : 502, headers: noStore });
  } catch {
    return Response.json({ error: "Scout API is unavailable" }, { status: 503, headers: noStore });
  }
}
