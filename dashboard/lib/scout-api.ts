import "server-only";
import { requireSession, sessionToken, appOrigin, currentSession } from "./auth";
import { sameOrigin } from "./auth-crypto";

export const apiUrl = () => process.env.CFO_API_URL ?? "http://api:8000";
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const session = await requireSession();
  const token = await sessionToken();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  headers.set("X-Scout-Organization", session.slug);
  return fetch(`${apiUrl()}${path}`, { ...init, cache: "no-store", redirect: "error", signal: init.signal ?? AbortSignal.timeout(15000), headers });
}
export async function checkWriteRequest(request: Request): Promise<Response | null> {
  if (!await currentSession()) return Response.json({ error: "Access denied" }, { status: 401 });
  if (!request.headers.get("content-type")?.startsWith("application/json")) return Response.json({ error: "Expected JSON" }, { status: 415 });
  if (!sameOrigin(request.headers.get("origin"), appOrigin())) return Response.json({ error: "Cross-site request refused" }, { status: 403 });
  return null;
}
export const noStore = { "Cache-Control": "private, no-store" };
