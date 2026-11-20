import { NextResponse, type NextRequest } from "next/server";
import { authorized } from "./lib/auth";

export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname === "/healthz") return NextResponse.next();
  const configured = process.env.DASHBOARD_USERNAME && process.env.DASHBOARD_PASSWORD;
  const response = authorized(request.headers.get("authorization"))
    ? NextResponse.next()
    : new NextResponse(configured ? "Authentication required" : "Dashboard access is not configured", {
        status: configured ? 401 : 503,
        headers: configured ? { "WWW-Authenticate": 'Basic realm="Lantern", charset="UTF-8"' } : {},
      });
  response.headers.set("Cache-Control", "private, no-store, max-age=0");
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  return response;
}
