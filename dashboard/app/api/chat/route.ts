import { authorized } from "@/lib/auth";
import { CHAT_MODELS, ChatInputError, parseChatRequest, readChatBody, type ChatEvent } from "@/lib/chat-model";
import { completionEvents } from "@/lib/chat-stream";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

export async function GET(request: Request) {
  if (!authorized(request.headers.get("authorization"))) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  return Response.json({ models: CHAT_MODELS }, { headers: privateHeaders });
}

export async function POST(request: Request) {
  if (!authorized(request.headers.get("authorization"))) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  let input: ReturnType<typeof parseChatRequest>;
  try { input = parseChatRequest(await readChatBody(request)); }
  catch (error) { return Response.json({ error: error instanceof ChatInputError ? error.message : "Invalid chat request." }, { status: 400, headers: privateHeaders }); }
  const abort = new AbortController();
  const signal = AbortSignal.any([request.signal, abort.signal, AbortSignal.timeout(120000)]);
  let upstream: Response;
  try {
    const base = process.env.LLM_CHAT_BASE_URL ?? "http://modelcar-granite-4-0-h-tiny-predictor.my-first-model.svc.cluster.local:8080/v1";
    const key = process.env.LLM_CHAT_API_KEY;
    upstream = await fetch(`${base.replace(/\/$/, "")}/chat/completions`, {
      method: "POST", cache: "no-store", redirect: "error", signal,
      headers: { "Content-Type": "application/json", ...(key ? { Authorization: `Bearer ${key}` } : {}) },
      body: JSON.stringify({ ...input, stream: true, max_tokens: 2048 }),
    });
    if (!upstream.ok) { await upstream.body?.cancel(); return Response.json({ error: "The model could not answer. Try again in a moment." }, { status: 502, headers: privateHeaders }); }
  } catch {
    return Response.json({ error: signal.aborted ? "The model took too long to respond. Try again." : "Could not connect to the model. Try again in a moment." }, { status: signal.aborted ? 504 : 502, headers: privateHeaders });
  }
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: ChatEvent) => controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
      try { for await (const event of completionEvents(upstream)) { if (signal.aborted) break; send(event); } }
      catch { if (!signal.aborted) send({ type: "error", message: "The response was interrupted. Try again." }); }
      finally { try { controller.close(); } catch { /* The client stopped the response. */ } }
    },
    cancel() { abort.abort(); },
  });
  return new Response(stream, { headers: { ...privateHeaders, "Content-Type": "application/x-ndjson; charset=utf-8", "X-Accel-Buffering": "no" } });
}
