import type { ChatEvent } from "./chat-model.ts";

async function* events(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let data: string[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      if (buffer.length + data.join("").length > 131072) throw new Error("Model event too large");
      let end: number;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end).replace(/\r$/, ""); buffer = buffer.slice(end + 1);
        if (line === "") { if (data.length) yield data.join("\n"); data = []; }
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (done) break;
    }
    if (buffer.startsWith("data:")) data.push(buffer.slice(5).trim());
    if (data.length) yield data.join("\n");
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

export async function* completionEvents(response: Response): AsyncGenerator<ChatEvent> {
  if (response.headers.get("content-type")?.includes("application/json")) {
    const body = await response.json();
    const choice = body?.choices?.[0];
    if (typeof choice?.message?.content !== "string" || !choice.message.content.trim()) throw new Error("No model answer");
    yield { type: "delta", text: choice.message.content };
    yield { type: "done", truncated: choice.finish_reason === "length" };
    return;
  }
  if (!response.body) throw new Error("No model stream");
  let answered = false;
  let ended = false;
  let finish: string | null = null;
  for await (const data of events(response.body)) {
    if (data === "[DONE]") { ended = true; break; }
    const item = JSON.parse(data);
    if (item.error) throw new Error("Model stream failed");
    const choice = item.choices?.[0];
    const text = choice?.delta?.content;
    if (typeof text === "string" && text) { answered = true; yield { type: "delta", text }; }
    if (typeof choice?.finish_reason === "string") finish = choice.finish_reason;
  }
  if (!answered || (!ended && !finish)) throw new Error("Model stream interrupted");
  yield { type: "done", truncated: finish === "length" };
}
