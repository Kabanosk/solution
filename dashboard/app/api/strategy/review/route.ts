import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
import { currentSession } from "@/lib/auth";

export const dynamic = "force-dynamic";
export async function POST(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const session = await currentSession();
  if (session?.role !== "owner") return Response.json({ error: "Only a workspace owner can run the strategy review." }, { status: 403, headers: noStore });
  try {
    const response = await apiFetch("/v1/strategy/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const body = await response.json();
    return Response.json(response.ok ? body : { error: "Could not queue the strategy review." }, { status: response.status, headers: noStore });
  } catch { return Response.json({ error: "Strategy review is temporarily unavailable." }, { status: 503, headers: noStore }); }
}
