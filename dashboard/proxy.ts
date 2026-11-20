import { NextResponse, type NextRequest } from "next/server";
import { authorized } from "./lib/auth";

const PUBLIC_PATHS = new Set(["/healthz", "/scout-icon.svg", "/scout-icon.png"]);

export function proxy(request: NextRequest) {
  // Health checks and the Slack avatar are public; everything else needs the dashboard login.
  if (PUBLIC_PATHS.has(request.nextUrl.pathname)) return NextResponse.next();
  const configured = process.env.DASHBOARD_USERNAME && process.env.DASHBOARD_PASSWORD;
  const allowed = authorized(request.headers.get("authorization"));
  // Tasks open in place on the dashboard; answer old /tasks/<id> links with a plain redirect instead of streaming a page.
  const task = /^\/tasks\/([^/]+)$/.exec(request.nextUrl.pathname);
  const response = allowed && task
    ? NextResponse.redirect(new URL(`/#task/${task[1]}`, request.url), 307)
    : allowed
    ? NextResponse.next()
    : new NextResponse(configured ? "Authentication required" : "Dashboard access is not configured", {
        status: configured ? 401 : 503,
        headers: configured ? { "WWW-Authenticate": 'Basic realm="Scout", charset="UTF-8"' } : {},
      });
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  return response;
}

// Hashed build assets (/_next/static) hold no company data and never change for a given URL: serve them without the
// login check and with Next's immutable cache headers. Before this, every page load re-downloaded ~200 KB of
// JavaScript because they were marked no-store.
export const config = {
  matcher: ["/((?!_next/static/).*)"],
};
