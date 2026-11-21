import { currentSession } from "@/lib/auth";
import { checkWriteRequest, noStore } from "@/lib/scout-api";
import { strategyDatabase } from "@/lib/strategy-store";
import { readChatBody } from "@/lib/chat-model";
import { parseGuardrailsToggle } from "@/lib/guardrails-model";
import { readGuardrails, saveGuardrails } from "@/lib/guardrails-store";
export const dynamic = "force-dynamic";
// The per-workspace NeMo guardrails switch (demo). Owner only. The control-plane pool (scout_auth) is shared with strategy.
const OWNER_ONLY = { error: "Workspace owner access required." };
export async function GET() {
  const session = await currentSession();
  if (session?.role !== "owner") return Response.json(OWNER_ONLY, { status: 403, headers: noStore });
  try { return Response.json(await readGuardrails(strategyDatabase(), session.organizationId), { headers: noStore }); }
  catch { return Response.json({ error: "The guardrails switch is unavailable." }, { status: 503, headers: noStore }); }
}
export async function PUT(request: Request) {
  const refused = await checkWriteRequest(request); if (refused) return refused;
  const session = await currentSession();
  if (session?.role !== "owner") return Response.json(OWNER_ONLY, { status: 403, headers: noStore });
  let enabled: boolean;
  try { enabled = parseGuardrailsToggle(await readChatBody(request)); }
  catch (error) { return Response.json({ error: error instanceof Error ? error.message : "Invalid request." }, { status: 400, headers: noStore }); }
  try { return Response.json(await saveGuardrails(strategyDatabase(), session, enabled), { headers: noStore }); }
  catch { return Response.json({ error: "Could not change the guardrails switch." }, { status: 503, headers: noStore }); }
}
