import { authorized } from "@/lib/auth";
import { getRecords } from "@/lib/finance";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

/** The audit log or the latest bank bookings: loaded when (or just before) their view opens, not with every page. */
export async function GET(request: Request, { params }: { params: Promise<{ kind: string }> }) {
  if (!authorized(request.headers.get("authorization"))) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  const { kind } = await params;
  if (kind !== "audit" && kind !== "transactions") return Response.json({ error: "Unknown records" }, { status: 404, headers: privateHeaders });
  try {
    return Response.json(await getRecords(kind), { headers: privateHeaders });
  } catch {
    return Response.json({ error: "Company data is temporarily unavailable" }, { status: 503, headers: privateHeaders });
  }
}
