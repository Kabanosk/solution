import "server-only";
import { headers } from "next/headers";
import { authorized } from "./auth";

// Server-side calls to the Scout API with the write token. The token never reaches the browser.
export const apiUrl = () => process.env.CFO_API_URL ?? "http://api:8000";

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = process.env.API_WRITE_TOKEN;
  if (!token) throw new Error("API_WRITE_TOKEN is not configured");
  return fetch(`${apiUrl()}${path}`, {
    ...init, cache: "no-store", signal: init.signal ?? AbortSignal.timeout(10000),
    headers: { ...init.headers, Authorization: `Bearer ${token}` },
  });
}

/** Dashboard login plus a same-origin JSON request (blocks cross-site form posts riding on Basic auth). */
export async function checkWriteRequest(request: Request): Promise<Response | null> {
  if (!authorized((await headers()).get("authorization"))) return Response.json({ error: "Access denied" }, { status: 401 });
  if (!request.headers.get("content-type")?.startsWith("application/json")) return Response.json({ error: "Expected JSON" }, { status: 415 });
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (origin && host && new URL(origin).host !== host) return Response.json({ error: "Cross-site request refused" }, { status: 403 });
  return null;
}

export const noStore = { "Cache-Control": "private, no-store" };
