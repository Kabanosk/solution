import { runStrategy } from "@/lib/strategy-store";
import { checkWriteRequest, noStore } from "@/lib/scout-api";
import { currentSession } from "@/lib/auth";

export const dynamic = "force-dynamic";
export async function POST(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const session = await currentSession();
  if (session?.role !== "owner") return Response.json({ error: "Only a workspace owner can run the strategy review." }, { status: 403, headers: noStore });
  try {
    return Response.json(await runStrategy(session.organizationId, true), { headers: noStore });
  } catch (error) {
    const setupError = error instanceof Error && ["Register a strategy provider first.", "Save company context before running research."].includes(error.message);
    return Response.json({ error: setupError ? error.message : "Strategy review is temporarily unavailable." }, { status: setupError ? 400 : 503, headers: noStore });
  }
}
