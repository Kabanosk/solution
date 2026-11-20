import { authorized } from "@/lib/auth";
import { getJobLog } from "@/lib/finance";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  if (!authorized(request.headers.get("authorization"))) {
    return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  }
  const { id } = await params;
  if (!id || id.length > 200) return Response.json({ error: "Invalid job ID" }, { status: 400, headers: privateHeaders });
  try {
    const entries = await getJobLog(id);
    if (entries === null) return Response.json({ error: "Job not found" }, { status: 404, headers: privateHeaders });
    return Response.json({ entries }, { headers: privateHeaders });
  } catch {
    return Response.json({ error: "Job log is temporarily unavailable" }, { status: 503, headers: privateHeaders });
  }
}
