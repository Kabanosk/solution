import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
import { authStore, currentSession } from "@/lib/auth";
import { readAuthBody } from "@/lib/auth-request";
import { mapBackendResult, parseSetupUpdate } from "@/lib/onboarding-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const reply = (body: unknown, status = 200) => Response.json(body, { status, headers: noStore });
const TIMEOUT_MS = 10000;

// GET /api/onboarding -> ${CFO_API_URL}/v1/onboarding. Any member of the current organization may read setup.
export async function GET() {
  try {
    const response = await apiFetch("/v1/onboarding", { signal: AbortSignal.timeout(TIMEOUT_MS) });
    const { status, body } = mapBackendResult(response.status, await response.json().catch(() => null));
    return reply(body, status);
  } catch (error) {
    if (error instanceof Error && error.message === "Access denied") return reply({ error: "Access denied." }, 401);
    return reply({ error: "Scout setup is temporarily unavailable." }, 503);
  }
}

// PATCH /api/onboarding {revision, action, ...}: owner only; the backend enforces the revision (409 on conflict).
export async function PATCH(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const session = await currentSession();
  if (!session || session.role !== "owner") return reply({ error: "Only a workspace owner can change setup." }, 403);
  let raw: Record<string, unknown>;
  try { raw = await readAuthBody(request); }
  catch (error) { const large = error instanceof Error && error.message.includes("large"); return reply({ error: large ? "Request too large." : "Invalid request." }, large ? 413 : 400); }
  const update = parseSetupUpdate(raw);
  if (!update.ok) return reply({ error: update.error }, 400);
  try {
    if (!(await authStore().rateLimit(`onboarding:write:${session.userId}:${session.organizationId}`, 120))) return reply({ error: "Too many requests. Try again later." }, 429);
    const response = await apiFetch("/v1/onboarding", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(update.value), signal: AbortSignal.timeout(TIMEOUT_MS) });
    const { status, body } = mapBackendResult(response.status, await response.json().catch(() => null));
    return reply(body, status);
  } catch { return reply({ error: "Scout setup is temporarily unavailable." }, 503); }
}
