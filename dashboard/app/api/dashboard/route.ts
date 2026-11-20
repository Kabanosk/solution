import { getDashboardData } from "@/lib/finance";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return Response.json(await getDashboardData(), { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    return Response.json({ error: "Company data is temporarily unavailable" }, { status: 503, headers: { "Cache-Control": "private, no-store" } });
  }
}
