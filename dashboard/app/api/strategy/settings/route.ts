import { currentSession } from "@/lib/auth";
import { checkWriteRequest, noStore } from "@/lib/scout-api";
import { saveStrategySettings, strategySettings } from "@/lib/strategy-store";
import { readChatBody } from "@/lib/chat-model";
export const dynamic = "force-dynamic";
export async function GET() {
  const session = await currentSession();
  if (session?.role !== "owner") return Response.json({ error: "Workspace owner access required." }, { status: 403, headers: noStore });
  try { return Response.json(await strategySettings(session.organizationId), { headers: noStore }); }
  catch { return Response.json({ error: "Strategy settings are unavailable." }, { status: 503, headers: noStore }); }
}
export async function PUT(request: Request) {
  const refused = await checkWriteRequest(request); if (refused) return refused;
  const session = await currentSession();
  if (session?.role !== "owner") return Response.json({ error: "Workspace owner access required." }, { status: 403, headers: noStore });
  try {
    const body = await readChatBody(request);
    return Response.json(await saveStrategySettings(session.organizationId, body as Record<string, unknown>), { headers: noStore });
  } catch (error) {
    const message = error instanceof Error && !('code' in error) ? error.message : "Could not save strategy settings.";
    return Response.json({ error: message }, { status: 400, headers: noStore });
  }
}
