import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
export const dynamic = "force-dynamic";

const SCENARIOS = new Set(["files", "email", "chat_refund", "nowhere", "overspend_chat", "ksef", "poisoned"]);

// Dev-only demo trigger (SCOUT_DEMO=1): POST {scenario} -> ${CFO_API_URL}/v1/demo/play/{scenario}
export async function POST(request: Request) {
  if (process.env.SCOUT_DEMO !== "1") return Response.json({ error: "Not found" }, { status: 404 });
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const body = await request.json().catch(() => null) as { scenario?: unknown } | null;
  if (typeof body?.scenario !== "string" || !SCENARIOS.has(body.scenario)) return Response.json({ error: "Unknown scenario" }, { status: 400, headers: noStore });
  try {
    const response = await apiFetch(`/v1/demo/play/${body.scenario}`, { method: "POST" });
    return Response.json(await response.json().catch(() => ({})), { status: response.ok ? 200 : 502, headers: noStore });
  } catch {
    return Response.json({ error: "Scout API is unavailable" }, { status: 503, headers: noStore });
  }
}
