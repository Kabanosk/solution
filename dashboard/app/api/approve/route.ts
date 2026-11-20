import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
export const dynamic = "force-dynamic";

// POST {id, action: "approve" | "reject"} -> ${CFO_API_URL}/v1/approvals/{id}:approve|:reject
export async function POST(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const body = await request.json().catch(() => null) as { id?: unknown; action?: unknown } | null;
  if (typeof body?.id !== "string" || !/^[\w.-]{1,80}$/.test(body.id) || (body.action !== "approve" && body.action !== "reject")) {
    return Response.json({ error: "Invalid approval" }, { status: 400, headers: noStore });
  }
  try {
    const response = await apiFetch(`/v1/approvals/${encodeURIComponent(body.id)}:${body.action}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ answered_by: process.env.SCOUT_CEO_ID ?? "vincenzo" }),
    });
    return Response.json(await response.json().catch(() => ({})), { status: response.ok ? 200 : 502, headers: noStore });
  } catch {
    return Response.json({ error: "Scout API is unavailable" }, { status: 503, headers: noStore });
  }
}
