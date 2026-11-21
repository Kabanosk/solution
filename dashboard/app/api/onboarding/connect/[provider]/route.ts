import { cookies } from "next/headers";
import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
import { appOrigin, authStore, cookieOptions, currentSession } from "@/lib/auth";
import { readAuthBody } from "@/lib/auth-request";
import { mapBackendResult, parseConnectBody, parseProvider, startProviderConnect, type OAuthProvider } from "@/lib/onboarding-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const reply = (body: unknown, status = 200) => Response.json(body, { status, headers: noStore });
const UNAVAILABLE = { error: "Scout setup is temporarily unavailable." };

async function owner(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return { response: refused };
  const session = await currentSession();
  if (!session || session.role !== "owner") return { response: reply({ error: "Only a workspace owner can change setup." }, 403) };
  if (!(await authStore().rateLimit(`onboarding:connect:${session.userId}:${session.organizationId}`, 60))) return { response: reply({ error: "Too many requests. Try again later." }, 429) };
  return { session };
}

// POST /api/onboarding/connect/{bunq|builtin|ksef|google|slack}. Only these five providers are forwarded; the
// backend path is built from the validated enum, never from the request URL.
export async function POST(request: Request, { params }: { params: Promise<{ provider: string }> }) {
  const provider = parseProvider((await params).provider);
  if (!provider) return reply({ error: "Setup option not found." }, 404);
  try {
    const guard = await owner(request);
    if (guard.response) return guard.response;
    let raw: Record<string, unknown>;
    try { raw = await readAuthBody(request); }
    catch (error) { const large = error instanceof Error && error.message.includes("large"); return reply({ error: large ? "Request too large." : "Invalid request." }, large ? 413 : 400); }
    if (provider === "google" || provider === "slack") {
      // Mailbox/install OAuth: server-derived redirect, separate provider-specific proof cookie (not the identity cookie).
      const started = await startProviderConnect({ provider: provider as OAuthProvider, body: raw, origin: appOrigin(), session: guard.session!, backend: (path, init) => apiFetch(path, { ...init, headers: { "Content-Type": "application/json" }, signal: AbortSignal.timeout(10000) }) });
      if (started.cookie) (await cookies()).set(started.cookie.name, started.cookie.value, { ...cookieOptions(started.cookie.maxAge), path: started.cookie.path });
      return reply(started.body, started.status);
    }
    const parsed = parseConnectBody(provider, raw);
    if (!parsed.ok) return reply({ error: parsed.error }, 400);
    // bunq/KSeF/built-in may need several provider calls, so apiFetch's own 15 s limit applies.
    const response = await apiFetch(`/v1/onboarding/connect/${provider}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(parsed.value) });
    const { status, body } = mapBackendResult(response.status, await response.json().catch(() => null));
    return reply(body, status);
  } catch { return reply(UNAVAILABLE, 503); }
}

// DELETE removes the local credential/configuration (the backend revokes at the provider with a short timeout).
export async function DELETE(request: Request, { params }: { params: Promise<{ provider: string }> }) {
  const provider = parseProvider((await params).provider);
  if (!provider) return reply({ error: "Setup option not found." }, 404);
  try {
    const guard = await owner(request);
    if (guard.response) return guard.response;
    const response = await apiFetch(`/v1/onboarding/connect/${provider}`, { method: "DELETE" });
    const { status, body } = mapBackendResult(response.status, await response.json().catch(() => null));
    return reply(body, status);
  } catch { return reply(UNAVAILABLE, 503); }
}
