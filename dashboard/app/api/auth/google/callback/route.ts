import { cookies } from "next/headers";
import { createRemoteJWKSet, jwtVerify } from "jose";
import { appOrigin, authStore, cookieOptions, currentSession, OAUTH_COOKIE, SESSION_COOKIE } from "@/lib/auth";
import { validateIdentity } from "@/lib/auth-crypto";
import { googleSignInRedirect } from "@/lib/onboarding-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const googleKeys = createRemoteJWKSet(new URL("https://www.googleapis.com/oauth2/v3/certs"));
export async function GET(request: Request) {
  const origin = appOrigin(), url = new URL(request.url), jar = await cookies();
  let slug = "", registering = false;
  const browser = jar.get(OAUTH_COOKIE)?.value ?? "";
  jar.set(OAUTH_COOKIE, "", cookieOptions(0));
  try {
    const states = url.searchParams.getAll("state"), codes = url.searchParams.getAll("code");
    if (states.length !== 1 || !browser) throw new Error("Invalid callback");
    const flow = await authStore().consumeOAuth(states[0], browser);
    if (!flow) throw new Error("Invalid state");
    slug = flow.slug;
    registering = Boolean(flow.registration);
    if (codes.length !== 1 || !codes[0] || codes[0].length > 4096 || url.searchParams.has("error")) throw new Error("Authorization refused");
    const client = process.env.SCOUT_GOOGLE_CLIENT_ID, secret = process.env.SCOUT_GOOGLE_CLIENT_SECRET;
    if (!client || !secret) throw new Error("Google login unavailable");
    const response = await fetch("https://oauth2.googleapis.com/token", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams({ client_id: client, client_secret: secret, grant_type: "authorization_code", code: codes[0], redirect_uri: `${origin}/api/auth/google/callback`, code_verifier: flow.verifier }), cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error("Token exchange failed");
    const tokens = await response.json();
    if (typeof tokens.id_token !== "string") throw new Error("Missing identity");
    const { payload } = await jwtVerify(tokens.id_token, googleKeys, { audience: client, issuer: ["https://accounts.google.com", "accounts.google.com"], algorithms: ["RS256"], maxTokenAge: "10m" });
    const identity = validateIdentity(payload, client, flow.nonce);
    if (flow.linkUserId) {
      const session = await currentSession(slug);
      if (session?.userId !== flow.linkUserId) throw new Error("Account changed during linking");
      await authStore().linkGoogle(session.userId, identity.subject, identity.email);
    }
    const result = flow.registration
      ? await authStore().registerGoogle(flow.registration, { ...identity, name: typeof payload.name === "string" && payload.name.trim() ? payload.name.trim().slice(0, 100) : identity.email.split("@")[0].slice(0, 100) })
      : await authStore().googleLogin(slug, identity.subject, identity.email);
    if (!result) return Response.redirect(`${origin}/${slug}?auth=no-membership`, 303);
    slug = result.session.slug;
    const old = jar.get(SESSION_COOKIE)?.value;
    if (old) await authStore().logout(old);
    jar.set(SESSION_COOKIE, result.token, cookieOptions());
    // Access/refresh tokens are deliberately not saved: sign-in is not a Gmail connection.
    return new Response(null, { status: 303, headers: { Location: googleSignInRedirect(origin, slug, { registered: Boolean(flow.registration), linked: Boolean(flow.linkUserId) }), "Cache-Control": "private, no-store" } });
  } catch {
    return new Response(null, { status: 303, headers: { Location: registering ? `${origin}/?create=1&auth=signup-failed` : `${origin}/${slug ? `${slug}?` : "?"}auth=failed`, "Cache-Control": "private, no-store" } });
  }
}
