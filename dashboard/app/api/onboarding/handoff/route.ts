import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
import { appOrigin, authStore, currentSession } from "@/lib/auth";
import { readAuthBody } from "@/lib/auth-request";
import { buildHandoff } from "@/lib/onboarding-handoff";
import { sanitizeSetup } from "@/lib/onboarding-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const reply = (body: unknown, status = 200) => Response.json(body, { status, headers: noStore });

// POST /api/onboarding/handoff {} -> {setupUrl, markdown, manifest, prompt, claudeUrl}. Pure allowlisted output:
// no token, cookie, mailbox, network answer or internal schema name, and sharing the link grants no access.
export async function POST(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const session = await currentSession();
  if (!session || session.role !== "owner") return reply({ error: "Only a workspace owner can create a setup handoff." }, 403);
  try { await readAuthBody(request); }
  catch (error) { const large = error instanceof Error && error.message.includes("large"); return reply({ error: large ? "Request too large." : "Invalid request." }, large ? 413 : 400); }
  try {
    if (!(await authStore().rateLimit(`onboarding:handoff:${session.userId}:${session.organizationId}`, 30))) return reply({ error: "Too many requests. Try again later." }, 429);
    const response = await apiFetch("/v1/onboarding", { signal: AbortSignal.timeout(10000) });
    // Unreadable state is an error, never a guess: the handoff must describe unconfigured services honestly.
    const setup = response.ok ? sanitizeSetup(await response.json().catch(() => null)) : null;
    if (!setup) return reply({ error: "Setup state is unavailable right now." }, 503);
    return reply(buildHandoff({ origin: appOrigin(), slug: session.slug, setup }));
  } catch { return reply({ error: "Setup handoff is temporarily unavailable." }, 503); }
}
