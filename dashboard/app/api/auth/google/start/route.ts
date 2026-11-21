import { createHash } from "node:crypto";
import { cookies } from "next/headers";
import { appOrigin, authStore, cookieOptions, currentSession, OAUTH_COOKIE } from "@/lib/auth";
import { newToken, sameOrigin, validSlug } from "@/lib/auth-crypto";
import { clientAddress } from "@/lib/auth-client-address";
import { readAuthBody } from "@/lib/auth-request";
import type { OrganizationDraft } from "@/lib/auth-store";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
async function start(request: Request, link: boolean, registration?: OrganizationDraft) {
  const origin = appOrigin(), url = new URL(request.url), slug = registration?.slug ?? url.searchParams.get("organization") ?? "";
  if (!validSlug(slug)) return Response.json({ error: "Check your organization URL." }, { status: 400 });
  if ((link || registration) && !sameOrigin(request.headers.get("origin"), origin)) return Response.json({ error: "Cross-site request refused" }, { status: 403 });
  const session = link ? await currentSession(slug) : null;
  if (link && !session) return new Response("Authentication required", { status: 401 });
  const client = process.env.SCOUT_GOOGLE_CLIENT_ID;
  if (!client || !process.env.SCOUT_GOOGLE_CLIENT_SECRET) return registration
    ? Response.json({ error: "Google sign-in is not configured yet." }, { status: 503 })
    : Response.redirect(`${origin}/${slug}?auth=google-unavailable`, 303);
  try {
    if (!(await authStore().rateLimit(`google-${registration ? "register" : "start"}:${clientAddress(request.headers)}`, registration ? 10 : 100))) return Response.json({ error: "Too many requests. Try again later." }, { status: 429 });
    const browser = newToken();
    const flow = registration ? await authStore().startGoogleRegistration(registration, browser) : await authStore().startOAuth(slug, browser, session?.userId);
    (await cookies()).set(OAUTH_COOKIE, browser, cookieOptions(600));
    const target = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    target.search = new URLSearchParams({ client_id: client, redirect_uri: `${origin}/api/auth/google/callback`, response_type: "code", scope: "openid email profile", state: flow.state, nonce: flow.nonce, code_challenge_method: "S256", code_challenge: createHash("sha256").update(flow.verifier).digest("base64url"), prompt: "select_account" }).toString();
    if (registration) return Response.json({ url: target.toString() }, { headers: { "Cache-Control": "private, no-store" } });
    return new Response(null, { status: 303, headers: { Location: target.toString(), "Cache-Control": "private, no-store" } });
  } catch (error) {
    if (registration) {
      const message = error instanceof Error && ["Check your organization name and URL.", "That organization URL is already registered."].includes(error.message) ? error.message : "Google registration is temporarily unavailable.";
      return Response.json({ error: message }, { status: 400, headers: { "Cache-Control": "private, no-store" } });
    }
    return Response.redirect(`${origin}/${slug}?auth=unavailable`, 303);
  }
}
export async function GET(request: Request) { return start(request, false); }
export async function POST(request: Request) {
  if (!sameOrigin(request.headers.get("origin"), appOrigin())) return new Response("Cross-site request refused", { status: 403 });
  if (request.headers.get("content-type")?.includes("application/json")) {
    try {
      const body = await readAuthBody(request);
      if (body.intent !== "register" || typeof body.name !== "string" || typeof body.slug !== "string") return Response.json({ error: "Check your organization name and URL." }, { status: 400 });
      return start(request, false, { name: body.name, slug: body.slug });
    } catch { return Response.json({ error: "Invalid registration request." }, { status: 400 }); }
  }
  return start(request, true);
}
