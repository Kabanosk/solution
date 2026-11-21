import { NextResponse, type NextRequest } from "next/server";
import { appOrigin, authStore, SESSION_COOKIE } from "./lib/auth";
import { sameOrigin, validSlug } from "./lib/auth-crypto";

const PUBLIC = new Set(["/", "/healthz", "/scout-icon.svg", "/scout-icon.png", "/google-g.png", "/icon.svg", "/favicon.ico", "/fonts/Inter.woff2", "/fonts/Mono.woff2"]);
export async function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname;
  const requestHeaders = new Headers(request.headers);
  // A client may request a tenant, but only the session/membership query can authorize it.
  requestHeaders.delete("x-scout-organization");
  requestHeaders.delete("x-scout-schema");
  let response: NextResponse;
  // OAuth provider navigation has no tenant header. Only these GET callbacks bypass the header check;
  // each callback still verifies the signed-in owner, browser proof and one-use provider state itself.
  const setupCallback = request.method === "GET" && /^\/api\/onboarding\/callback\/(google|slack)$/.test(path);
  if (path.startsWith("/api/auth/") || path.startsWith("/join/") || path.startsWith("/login/") || PUBLIC.has(path) || setupCallback) {
    response = NextResponse.next({ request: { headers: requestHeaders } });
  } else if (path.startsWith("/api/")) {
    const slug = request.headers.get("x-scout-organization") ?? "";
    try {
      if (!validSlug(slug) || !await authStore().session(request.cookies.get(SESSION_COOKIE)?.value, slug)) response = NextResponse.json({ error: "Authentication required" }, { status: 401 });
      else if (!["GET", "HEAD", "OPTIONS"].includes(request.method) && !sameOrigin(request.headers.get("origin"), appOrigin())) response = NextResponse.json({ error: "Cross-site request refused" }, { status: 403 });
      else { requestHeaders.set("x-scout-organization", slug); response = NextResponse.next({ request: { headers: requestHeaders } }); }
    } catch { response = NextResponse.json({ error: "Authentication temporarily unavailable" }, { status: 503 }); }
  } else {
    const slug = path.slice(1);
    if (!validSlug(slug)) response = NextResponse.redirect(new URL("/", request.url), 303);
    else { requestHeaders.set("x-scout-organization", slug); response = NextResponse.next({ request: { headers: requestHeaders } }); }
  }
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  response.headers.set("Referrer-Policy", "same-origin");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");
  return response;
}
export const config = { matcher: ["/((?!_next/static/|_next/image).*)"] };
