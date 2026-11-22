import { currentSession } from "@/lib/auth";
import { checkWriteRequest, noStore } from "@/lib/scout-api";
import { strategyDatabase } from "@/lib/strategy-store";
import { readChatBody } from "@/lib/chat-model";
import { guardrailLocksAccess, parseUnlock } from "@/lib/guardrails-model";
import { listGuardrailLocks, unlockGuardrailPerson } from "@/lib/guardrails-store";
export const dynamic = "force-dynamic";
// The people locked out of Scout after three blocked messages (T087): list them, lift a lock. Owner only, like the
// guardrails switch next to it. The lock and audit tables are in the workspace's own schema.
export async function GET() {
  const session = await currentSession();
  const denied = guardrailLocksAccess(session?.role);
  if (!session || denied) return Response.json({ error: denied?.error ?? "Access denied" }, { status: denied?.status ?? 401, headers: noStore });
  try { return Response.json(await listGuardrailLocks(strategyDatabase(), session), { headers: noStore }); }
  catch { return Response.json({ error: "The locked people could not be loaded." }, { status: 503, headers: noStore }); }
}
export async function POST(request: Request) {
  const refused = await checkWriteRequest(request); if (refused) return refused;
  const session = await currentSession();
  const denied = guardrailLocksAccess(session?.role);
  if (!session || denied) return Response.json({ error: denied?.error ?? "Access denied" }, { status: denied?.status ?? 401, headers: noStore });
  let person: string;
  try { person = parseUnlock(await readChatBody(request)); }
  catch (error) { return Response.json({ error: error instanceof Error ? error.message : "Invalid request." }, { status: 400, headers: noStore }); }
  try {
    const db = strategyDatabase();
    const unlocked = await unlockGuardrailPerson(db, session, person);
    return Response.json({ unlocked, locks: await listGuardrailLocks(db, session) }, { headers: noStore });
  } catch { return Response.json({ error: "Could not unlock this person." }, { status: 503, headers: noStore }); }
}
