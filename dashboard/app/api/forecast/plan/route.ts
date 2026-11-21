import { apiFetch, checkWriteRequest, noStore } from "@/lib/scout-api";
import { currentSession } from "@/lib/auth";
import { readAuthBody } from "@/lib/auth-request";
import { backendError, moneyValue, parsePlanInput, sanitizePlan } from "@/lib/revenue-plan-model";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const reply = (body: unknown, status = 200) => Response.json(body, { status, headers: noStore });
const OWNER_ONLY = "Only a workspace owner can configure the forecast plan.";
const UNAVAILABLE = "The forecast plan is temporarily unavailable.";

const decimal = (value: unknown) => moneyValue(value) === null ? "0" : String(value);

export async function GET() {
  const session = await currentSession();
  if (!session) return reply({ error: "Access denied" }, 401);
  if (session.role !== "owner") return reply({ error: OWNER_ONLY }, 403);
  try {
    const upstream = await apiFetch("/v1/forecast/plan", { signal: AbortSignal.timeout(10000) });
    const data = await upstream.json().catch(() => null);
    if (!upstream.ok) { const failed = backendError(upstream.status, data, UNAVAILABLE); return reply({ error: failed.error }, failed.status); }
    const defaults = data && typeof data === "object" && "defaults" in data && data.defaults && typeof data.defaults === "object" ? data.defaults as Record<string, unknown> : {};
    return reply({
      plan: sanitizePlan((data as Record<string, unknown> | null)?.plan),
      defaults: { trailing_revenue: decimal(defaults.trailing_revenue), default_target: decimal(defaults.default_target), currency: "EUR" },
    });
  } catch { return reply({ error: UNAVAILABLE }, 503); }
}

export async function PUT(request: Request) {
  const refused = await checkWriteRequest(request);
  if (refused) return refused;
  const session = await currentSession();
  if (session?.role !== "owner") return reply({ error: OWNER_ONLY }, 403);
  let raw: Record<string, unknown>;
  try { raw = await readAuthBody(request); }
  catch (error) { const large = error instanceof Error && error.message.includes("large"); return reply({ error: large ? "Request too large." : "Invalid request." }, large ? 413 : 400); }
  const parsed = parsePlanInput(raw);
  if (!parsed.ok) return reply({ error: parsed.error }, 400);
  try {
    const upstream = await apiFetch("/v1/forecast/plan", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(parsed.value), signal: AbortSignal.timeout(10000) });
    const data = await upstream.json().catch(() => null);
    if (!upstream.ok) { const failed = backendError(upstream.status, data, "Could not save the forecast plan."); return reply({ error: failed.error }, failed.status); }
    const plan = sanitizePlan((data as Record<string, unknown> | null)?.plan);
    if (!plan) return reply({ error: "The forecast plan returned an unexpected response." }, 502);
    return reply({ plan, queued: (data as Record<string, unknown>).queued === true });
  } catch { return reply({ error: UNAVAILABLE }, 503); }
}
