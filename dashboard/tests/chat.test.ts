import { test } from "node:test";
import assert from "node:assert/strict";
import { CHAT_MODELS, ChatInputError, conversationForRequest, parseChatRequest, readChatBody, readJsonLines, type ChatTurn } from "../lib/chat-model.ts";
import { completionEvents } from "../lib/chat-stream.ts";

const model = CHAT_MODELS[0].id;
const user = { role: "user", content: "Hello" };
function stream(text: string): ReadableStream<Uint8Array> {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i++) controller.enqueue(bytes.slice(i, i + 1)); controller.close(); } });
}
async function collect<T>(source: AsyncIterable<T>): Promise<T[]> { const values: T[] = []; for await (const value of source) values.push(value); return values; }

test("chat accepts only the configured model and bounded user/assistant conversation", () => {
  assert.deepEqual(parseChatRequest({ model, messages: [user], extra: "ignored" }), { model, messages: [user] });
  for (const value of [null, { model: "other", messages: [user] }, { model, messages: [] }, { model, messages: [{ role: "system", content: "Hi" }] },
    { model, messages: [{ ...user, content: " " }] }, { model, messages: [user, user] },
    { model, messages: [user, { role: "assistant", content: "Hi" }] }, { model, messages: [{ ...user, content: "x".repeat(16001) }] }]) {
    assert.throws(() => parseChatRequest(value), ChatInputError);
  }
  const messages = [user, { role: "assistant", content: "Hello" }, { ...user, content: "Continue" }];
  assert.deepEqual(parseChatRequest({ model, messages }).messages, messages);
  const huge = Array.from({ length: 7 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: "x".repeat(10000) }));
  assert.throws(() => parseChatRequest({ model, messages: huge }), /too long/);
});

test("failed exchanges are not invented context; stopped partial replies remain usable", () => {
  const turn = (role: ChatTurn["role"], content: string, status: ChatTurn["status"] = "complete"): ChatTurn => ({ id: crypto.randomUUID(), role, content, status });
  const turns = [turn("user", "Earlier"), turn("assistant", "No answer", "error"), turn("user", "Second"), turn("assistant", "Partial", "stopped"), turn("user", "Continue")];
  const messages = conversationForRequest(turns);
  assert.deepEqual(messages, [{ role: "user", content: "Second" }, { role: "assistant", content: "Partial" }, { role: "user", content: "Continue" }]);
  assert.doesNotThrow(() => parseChatRequest({ model, messages }));
});

test("request bodies are byte-bounded and malformed JSON is rejected", async () => {
  const request = new Request("http://localhost/api/chat", { method: "POST", body: JSON.stringify({ model, messages: [user] }) });
  assert.deepEqual(await readChatBody(request), { model, messages: [user] });
  await assert.rejects(readChatBody(new Request("http://localhost", { method: "POST", body: "invalid" })), ChatInputError);
  await assert.rejects(readChatBody(new Request("http://localhost", { method: "POST", body: "x".repeat(262145) })), /too long/);
});

test("Granite SSE survives split UTF-8/chunks, CRLF and metadata; hidden reasoning is not forwarded", async () => {
  const sse = ': comment\r\n\r\ndata: {"choices":[{"delta":{"role":"assistant","reasoning_content":"private"}}]}\r\n\r\ndata: {"choices":[{"delta":{"content":"Cześć 👋"}}]}\r\n\r\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\r\n\r\ndata: [DONE]\r\n\r\n';
  const response = new Response(stream(sse), { headers: { "content-type": "text/event-stream" } });
  assert.deepEqual(await collect(completionEvents(response)), [{ type: "delta", text: "Cześć 👋" }, { type: "done", truncated: false }]);
});

test("empty and broken provider streams fail; length limits and JSON fallback are preserved", async () => {
  await assert.rejects(collect(completionEvents(new Response(stream('data: [DONE]\n\n')))));
  await assert.rejects(collect(completionEvents(new Response(stream('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')))));
  const truncated = new Response(stream('data: {"choices":[{"delta":{"content":"short"},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n'));
  assert.deepEqual((await collect(completionEvents(truncated))).at(-1), { type: "done", truncated: true });
  const fallback = Response.json({ choices: [{ message: { content: "Hello" }, finish_reason: "stop" }] });
  assert.deepEqual(await collect(completionEvents(fallback)), [{ type: "delta", text: "Hello" }, { type: "done", truncated: false }]);
});

test("browser framing handles split JSON and a final line without newline", async () => {
  const text = '{"type":"delta","text":"Cześć"}\n{"type":"done","truncated":false}';
  assert.deepEqual(await collect(readJsonLines(stream(text))), [{ type: "delta", text: "Cześć" }, { type: "done", truncated: false }]);
});
