import { authorized } from "@/lib/auth";
import { getTaskDetail } from "@/lib/finance";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

/** One task with steps, approval and audit, so the dashboard can open it in place without a page load. */
export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!authorized(request.headers.get("authorization"))) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  const { id } = await params;
  try {
    const detail = await getTaskDetail(decodeURIComponent(id));
    if (!detail) return Response.json({ error: "No such task" }, { status: 404, headers: privateHeaders });
    return Response.json(detail, { headers: privateHeaders });
  } catch {
    return Response.json({ error: "Company data is temporarily unavailable" }, { status: 503, headers: privateHeaders });
  }
}
