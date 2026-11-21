import { currentSession } from "@/lib/auth";
import { CHAT_MODELS, ChatInputError, parseChatRequest, readChatBody, readJsonLines, sanitizeChatEvent, type ChatEvent } from "@/lib/chat-model";
import { apiFetch, checkWriteRequest } from "@/lib/scout-api";
import { externalChat, ExternalChatError } from "@/lib/external-chat";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store" };

export async function GET(request: Request) {
  if (!await currentSession()) return Response.json({ error: "Access denied" }, { status: 401, headers: privateHeaders });
  return Response.json({ models: CHAT_MODELS }, { headers: privateHeaders });
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
  const signal = AbortSignal.any([request.signal, abort.signal, AbortSignal.timeout(120000)]);
  if (input.external) {
    const config = input.external;
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const send = (event: ChatEvent) => controller.enqueue(encoder.encode(JSON.stringify(event) + "\n"));
        try {
          for await (const event of externalChat(config, input.messages, signal, {
            forecast: intent => apiFetch("/v1/chat/forecast", {
              method: "POST", signal, headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
              body: JSON.stringify({ ...intent, ...(input.conversationId ? { conversation_id: input.conversationId } : {}) }),
            }),
          })) { if (!signal.aborted) send(event); }
        } catch (error) {
          if (!signal.aborted) send({ type: "error", message: error instanceof ExternalChatError ? error.message : "The forecast could not finish. Try again." });
        } finally { try { controller.close(); } catch { /* Client stopped the response. */ } }
      },
      cancel() { abort.abort(); },
    });
    return new Response(stream, { headers: { ...privateHeaders, "Content-Type": "application/x-ndjson; charset=utf-8", "X-Accel-Buffering": "no" } });
  }
  let upstream: Response;
  try {
    upstream = await apiFetch("/v1/chat", {
      method: "POST", redirect: "error", signal,
      headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
      body: JSON.stringify({ messages: input.messages, ...(input.conversationId ? { conversation_id: input.conversationId } : {}) }),
    });
    if (!upstream.ok || !upstream.body) { await upstream.body?.cancel(); return Response.json({ error: "Scout could not answer. Try again in a moment." }, { status: 502, headers: privateHeaders }); }
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
          if (!event) continue;
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
