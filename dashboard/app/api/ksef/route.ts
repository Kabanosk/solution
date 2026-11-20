import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
export const dynamic = "force-dynamic";

// "Connect KSeF": POST -> ${CFO_API_URL}/v1/connections/ksef:connect. 202 {connection, job_id}; 409 already_running.
// Not gated by SCOUT_DEMO: it talks to the real KSeF test environment (fictitious data only).
export async function POST(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  try {
    const response = await apiFetch("/v1/connections/ksef:connect", { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } });
    const payload = await response.json().catch(() => ({}));
    return Response.json(payload, { status: response.status, headers: noStore });
  } catch {
    return Response.json({ error: "Scout API is unavailable" }, { status: 503, headers: noStore });
  }
}
