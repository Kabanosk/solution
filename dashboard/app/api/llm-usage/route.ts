import { authorized } from "@/lib/auth";
import { getLlmUsage } from "@/lib/llm-usage";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

export async function GET(request: Request) {
  if (!authorized(request.headers.get("authorization"))) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  const range = new URL(request.url).searchParams.get("days") ?? "7";
  if (!["1", "7", "30"].includes(range)) return Response.json({ error: "Invalid range" }, { status: 400, headers: privateHeaders });
  const data = await getLlmUsage(Number(range));
  return Response.json(data, { status: data.reportUnavailable && data.snapshotUnavailable ? 503 : 200, headers: privateHeaders });
}
