import { headers } from "next/headers";
import { authorized } from "@/lib/auth";
import { apiFetch } from "@/lib/scout-api";
export const dynamic = "force-dynamic";

// Starts Google OAuth: the API returns {url} (or redirects); the browser is sent there.
export async function GET(request: Request) {
  if (!authorized((await headers()).get("authorization"))) return new Response("Access denied", { status: 401 });
  const back = new URL("/connections?google=error", request.url);
  try {
    const response = await apiFetch("/v1/connections/google/start", { redirect: "manual" });
    const target = response.status >= 300 && response.status < 400 ? response.headers.get("location")
      : response.ok ? (await response.json().catch(() => null) as { url?: string } | null)?.url : null;
    if (!target || !target.startsWith("https://")) return Response.redirect(back, 303);
    return Response.redirect(target, 303);
  } catch {
    return Response.redirect(back, 303);
  }
}
