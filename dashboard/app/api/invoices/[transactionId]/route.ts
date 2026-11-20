import { authorized } from "@/lib/auth";
import { parseTransactionInvoice } from "@/lib/invoice-model";
import { apiFetch } from "@/lib/scout-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

/** The invoice filed for a bank booking (Scout API GET /v1/transactions/{id}/invoice), read server-side. */
export async function GET(request: Request, { params }: { params: Promise<{ transactionId: string }> }) {
  if (!authorized(request.headers.get("authorization"))) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  const { transactionId } = await params;
  const id = decodeURIComponent(transactionId);
  if (!/^[\w.:-]{1,128}$/.test(id)) return Response.json({ error: "Invalid transaction" }, { status: 400, headers: privateHeaders });
  try {
    const response = await apiFetch(`/v1/transactions/${encodeURIComponent(id)}/invoice`, { signal: AbortSignal.timeout(8000) });
    if (response.status === 404) return Response.json({ error: "No invoice is filed for this payment." }, { status: 404, headers: privateHeaders });
    if (!response.ok) throw new Error("Invoice unavailable");
    return Response.json(parseTransactionInvoice(await response.json()), { headers: privateHeaders });
  } catch {
    return Response.json({ error: "The invoice could not be loaded. Try again shortly." }, { status: 503, headers: privateHeaders });
  }
}
