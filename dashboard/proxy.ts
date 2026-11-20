import { NextResponse, type NextRequest } from "next/server";
import { authorized } from "./lib/auth";

const PUBLIC_PATHS = new Set(["/healthz", "/scout-icon.svg", "/scout-icon.png"]);

export function proxy(request: NextRequest) {
  // Health checks and the Slack avatar are public; everything else needs the dashboard login.
  if (PUBLIC_PATHS.has(request.nextUrl.pathname)) return NextResponse.next();
  const configured = process.env.DASHBOARD_USERNAME && process.env.DASHBOARD_PASSWORD;
  const response = authorized(request.headers.get("authorization"))
    ? NextResponse.next()
    : new NextResponse(configured ? "Authentication required" : "Dashboard access is not configured", {
        status: configured ? 401 : 503,
        headers: configured ? { "WWW-Authenticate": 'Basic realm="Scout", charset="UTF-8"' } : {},
      });
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  return response;
}
