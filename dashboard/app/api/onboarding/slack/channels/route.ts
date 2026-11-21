import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
import { authStore, currentSession } from "@/lib/auth";
import { readAuthBody } from "@/lib/auth-request";
import { mapBackendResult, parseSlackChannels } from "@/lib/onboarding-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const reply = (body: unknown, status = 200) => Response.json(body, { status, headers: noStore });

export async function PUT(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  try {
    const session = await currentSession();
    if (!session || session.role !== "owner") return reply({ error: "Only a workspace owner can choose Slack channels." }, 403);
    if (!await authStore().rateLimit(`onboarding:channels:${session.userId}:${session.organizationId}`, 60)) return reply({ error: "Too many requests. Try again later." }, 429);
    let raw: Record<string, unknown>;
    try { raw = await readAuthBody(request); }
    catch (error) { const large = error instanceof Error && error.message.includes("large"); return reply({ error: large ? "Request too large." : "Invalid request." }, large ? 413 : 400); }
    const parsed = parseSlackChannels(raw);
    if (!parsed.ok) return reply({ error: parsed.error }, 400);
    const upstream = await apiFetch("/v1/onboarding/slack/channels", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(parsed.value), signal: AbortSignal.timeout(10000) });
    const result = mapBackendResult(upstream.status, await upstream.json().catch(() => null));
    return reply(result.body, result.status);
  } catch { return reply({ error: "Slack setup is temporarily unavailable." }, 503); }
}
