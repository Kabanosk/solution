import { configuredStrategyProvider, SERVER_PROVIDER_KEY } from "@/lib/strategy-store";
import { currentSession } from "@/lib/auth";
import { CHAT_MODELS, ChatInputError, parseChatRequest, readChatBody, readJsonLines, sanitizeChatEvent, type ChatEvent } from "@/lib/chat-model";
import { apiFetch, checkWriteRequest } from "@/lib/scout-api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

export async function GET(request: Request) {
  const session = await currentSession();
  if (!session) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  let provider = null;
  if (session.role === "owner") {
    try { provider = await configuredStrategyProvider(session.organizationId); } catch { /* Chat remains usable if strategy storage is unavailable. */ }
  }
  return Response.json({ models: CHAT_MODELS, external: provider ? { provider: provider.provider, model: provider.model, apiKey: SERVER_PROVIDER_KEY } : null }, { headers: privateHeaders });
}

// Scout chat: proxies to the Scout API's POST /v1/chat (which runs Scout's tools) with the server-only write token,
// and passes its NDJSON events (step, delta, done, error) through to the browser after validation.
export async function POST(request: Request) {
  const denied = await checkWriteRequest(request);
  if (denied) return denied;
  let input: ReturnType<typeof parseChatRequest>;
  try { input = parseChatRequest(await readChatBody(request)); }
  catch (error) { return Response.json({ error: error instanceof ChatInputError ? error.message : "Invalid chat request." }, { status: 400, headers: privateHeaders }); }
  const abort = new AbortController();
  const signal = AbortSignal.any([request.signal, abort.signal, AbortSignal.timeout(input.external ? 180000 : 120000)]);
  // An external model (e.g. OpenAI GPT) runs Scout's own agent loop with the same tools as Granite. The provider
  // credentials go to the Scout API for this request only; the API fixes the provider's base URL.
  let external: { provider: string; model: string; api_key: string } | undefined;
  if (input.external) {
    let config = input.external;
    if (config.apiKey === SERVER_PROVIDER_KEY) {
      const session = await currentSession();
      if (!session) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
      if (session.role !== "owner") return Response.json({ error: "Workspace owner access required." }, { status: 403, headers: privateHeaders });
      try {
        const saved = await configuredStrategyProvider(session.organizationId);
        if (!saved) throw new Error();
        config = saved;
      } catch { return Response.json({ error: "The workspace provider is unavailable." }, { status: 503, headers: privateHeaders }); }
    }
    external = { provider: config.provider, model: config.model, api_key: config.apiKey };
  }
  let upstream: Response;
  try {
    upstream = await apiFetch("/v1/chat", {
      method: "POST", redirect: "error", signal,
      headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
      body: JSON.stringify({ messages: input.messages, ...(input.conversationId ? { conversation_id: input.conversationId } : {}), ...(external ? { external } : {}) }),
    });
    if (!upstream.ok || !upstream.body) { await upstream.body?.cancel(); return Response.json({ error: external ? "Scout could not answer with the external model. Check the provider, model and API key." : "Scout could not answer. Try again in a moment." }, { status: 502, headers: privateHeaders }); }
  } catch {
    return Response.json({ error: signal.aborted ? "Scout took too long to respond. Try again." : "Could not connect to Scout. Try again in a moment." }, { status: signal.aborted ? 504 : 502, headers: privateHeaders });
  }
  const body = upstream.body;
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: ChatEvent) => controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
      let finished = false;
      try {
        for await (const raw of readJsonLines(body)) {
          if (signal.aborted) break;
          const event = sanitizeChatEvent(raw);
          if (!event || event.type === "external_context") continue;
          send(event);
          if (event.type === "done" || event.type === "error") { finished = true; break; }
        }
        if (!finished && !signal.aborted) send({ type: "error", message: "The response was interrupted. Try again." });
      }
      catch { if (!signal.aborted) send({ type: "error", message: "The response was interrupted. Try again." }); }
      finally { try { controller.close(); } catch { /* The client stopped the response. */ } }
    },
    cancel() { abort.abort(); },
  });
  return new Response(stream, { headers: { ...privateHeaders, "Content-Type": "application/x-ndjson; charset=utf-8", "X-Accel-Buffering": "no" } });
}
