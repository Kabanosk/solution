import { test } from "node:test";
import assert from "node:assert/strict";
import { applyCompaction, CHAT_MODELS, ChatInputError, conversationForRequest, mergeFollowups, newConversationId, requestFor, SUMMARY_PREFIX, unacknowledgedFollowups, parseChatRequest, readChatBody, readJsonLines, sanitizeChatEvent, type ChatTurn } from "../lib/chat-model.ts";
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
  const huge = Array.from({ length: 13 }, (_, i) => ({ role: i % 2 ? "assistant" : "user", content: "x".repeat(10000) }));
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
  await assert.rejects(readChatBody(new Request("http://localhost", { method: "POST", body: "x".repeat(524289) })), /too long/);
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

test("Scout NDJSON: tool steps, the answer and the task id pass through in order", async () => {
  const text = '{"type":"step","text":"Looked at missing invoices(days=30): 1 result(s)"}\n{"type":"step","text":"Checked the cash forecast"}\n'
    + '{"type":"delta","text":"One payment is missing an invoice."}\n{"type":"done","truncated":false,"task_id":"SC-123"}\n';
  const events = (await collect(readJsonLines(stream(text)))).map(sanitizeChatEvent);
  assert.deepEqual(events, [
    { type: "step", text: "Looked at missing invoices(days=30): 1 result(s)" },
    { type: "step", text: "Checked the cash forecast" },
    { type: "delta", text: "One payment is missing an invoice." },
    { type: "done", truncated: false, task_id: "SC-123" },
  ]);
});

test("Scout events are validated before reaching the browser", () => {
  assert.equal(sanitizeChatEvent({ type: "tool_call", name: "secret" }), null);
  assert.equal(sanitizeChatEvent({ type: "step", text: "  " }), null);
  assert.equal(sanitizeChatEvent(null), null);
  assert.deepEqual(sanitizeChatEvent({ type: "done", truncated: false, task_id: "../etc" }), { type: "done", truncated: false });
  assert.deepEqual(sanitizeChatEvent({ type: "done" }), { type: "done", truncated: false });
  assert.deepEqual(sanitizeChatEvent({ type: "error" }), { type: "error", message: "Scout could not answer. Try again." });
  assert.equal((sanitizeChatEvent({ type: "step", text: "x".repeat(900) }) as { text: string }).text.length, 500);
});

test("file events are validated; unknown events stay dropped", () => {
  const csv = Buffer.from("number,total\nFLV-1,120.00\n").toString("base64");
  assert.deepEqual(sanitizeChatEvent({ type: "file", name: "../../invoices-2026-11-21.csv", mime: "text/csv", data_b64: csv, sent_to_slack: true }),
    { type: "file", name: "invoices-2026-11-21.csv", mime: "text/csv", data_b64: csv, sent_to_slack: true });
  assert.equal((sanitizeChatEvent({ type: "file", name: "x.html", mime: "text/html", data_b64: csv }) as { mime: string }).mime, "application/octet-stream");
  assert.equal(sanitizeChatEvent({ type: "file", name: "x.csv", mime: "text/csv", data_b64: "not base64!" }), null);
  assert.equal(sanitizeChatEvent({ type: "file", name: "x.csv", mime: "text/csv" }), null);
  assert.equal(sanitizeChatEvent({ type: "surprise", text: "x" }), null);
});

const t = (id: string, role: ChatTurn["role"], content = id, status: ChatTurn["status"] = "complete"): ChatTurn => ({ id, role, content, status });

test("compacted events are validated", () => {
  assert.deepEqual(sanitizeChatEvent({ type: "compacted", summary: " Paid Asimex €612. ", replaced: 6 }), { type: "compacted", summary: "Paid Asimex €612.", replaced: 6 });
  assert.equal(sanitizeChatEvent({ type: "compacted", summary: "", replaced: 6 }), null);
  assert.equal(sanitizeChatEvent({ type: "compacted", summary: "x", replaced: 0 }), null);
  assert.equal(sanitizeChatEvent({ type: "compacted", summary: "x", replaced: 2.5 }), null);
});

test("compaction replaces the summarized turns with one summary item, which is sent as an assistant message", () => {
  const turns = [t("u1", "user"), t("a1", "assistant"), t("u2", "user"), t("a2", "assistant", "", "error"), t("u3", "user"), t("a3", "assistant"),
    t("u4", "user"), t("a4", "assistant"), t("u5", "user")];
  const { messages, sources } = requestFor(turns);
  assert.deepEqual(sources, ["u1", "a1", "u3", "a3", "u4", "a4", "u5"], "the failed u2 exchange is not sent");
  assert.equal(messages.length, 7);
  const pending = [...turns, t("p", "assistant", "", "pending")];
  const compacted = applyCompaction(pending, sources, { summary: "Earlier: **€612** to Asimex.", replaced: 4 }, "s1");
  assert.deepEqual(compacted.map(turn => turn.id), ["s1", "u4", "a4", "u5", "p"]);
  assert.equal(compacted[0].kind, "summary");
  assert.equal(compacted[0].replaced, 4);
  const next = requestFor([...compacted.slice(0, 4), t("p", "assistant", "ok"), t("u6", "user")]);
  assert.deepEqual(next.messages[0], { role: "assistant", content: `${SUMMARY_PREFIX}\nEarlier: **€612** to Asimex.` });
  assert.deepEqual(next.messages.slice(1).map(message => message.role), ["user", "assistant", "user", "assistant", "user"]);
  assert.doesNotThrow(() => parseChatRequest({ model, messages: next.messages }), "the route accepts a leading summary");
  // A second compaction folds the old summary in and keeps counting the original messages.
  const again = applyCompaction([...compacted.slice(0, 4), t("p", "assistant", "ok"), t("u6", "user")], next.sources, { summary: "All of it.", replaced: 4 }, "s2");
  assert.deepEqual(again.map(turn => turn.id), ["s2", "p", "u6"]);
  assert.equal(again[0].replaced, 4 - 1 + 4);
  assert.equal(applyCompaction(turns, [], { summary: "x", replaced: 2 }, "s3"), turns, "nothing known: unchanged");
});

test("a leading assistant message must be a summary", () => {
  assert.throws(() => parseChatRequest({ model, messages: [{ role: "assistant", content: "Hi" }, user] }), ChatInputError);
});

test("very long uncompacted chats send only the newest exchanges that fit", () => {
  const turns: ChatTurn[] = [];
  for (let i = 0; i < 40; i++) turns.push(t(`u${i}`, "user", "q"), t(`a${i}`, "assistant", "a"));
  turns.push(t("last", "user", "now"));
  const { messages, sources } = requestFor(turns);
  assert.ok(messages.length <= 62);
  assert.equal(messages[0].role, "user");
  assert.equal(sources.at(-1), "last");
  assert.doesNotThrow(() => parseChatRequest({ model, messages }));
});

test("a chat request carries its conversation id, and only a well-formed one", () => {
  const model = CHAT_MODELS[0].id;
  const user = { role: "user" as const, content: "hi" };
  assert.deepEqual(parseChatRequest({ model, messages: [user], conversationId: "abc12345def" }),
    { model, messages: [user], conversationId: "abc12345def" });
  assert.throws(() => parseChatRequest({ model, messages: [user], conversationId: "bad id!" }), ChatInputError);
  assert.match(newConversationId(), /^[A-Za-z0-9_-]{8,64}$/);
});

test("follow-ups attach once to the answer waiting for them and go back to the model", () => {
  const turns: ChatTurn[] = [
    { id: "u1", role: "user", content: "what if an oven", status: "complete" },
    { id: "a1", role: "assistant", content: "Running the forecast.", status: "complete", taskId: "SC-1", awaiting: true },
    { id: "u2", role: "user", content: "and today?", status: "complete" },
    { id: "a2", role: "assistant", content: "Friday.", status: "complete", taskId: "SC-1" },
  ];
  const steps = [{ id: 7, kind: "followup", text: "Wojciech approved it." }, { id: 5, kind: "looked_up", text: "x" }];
  const merged = mergeFollowups(turns, steps);
  assert.deepEqual(merged[1].followups, [{ id: 7, text: "Wojciech approved it." }]);
  assert.equal(mergeFollowups(merged, steps), merged);  // nothing new: same array
  assert.match(requestFor(merged).messages[1].content, /Update: Wojciech approved it\./);
  assert.equal(sanitizeChatEvent({ type: "done", truncated: false, task_id: "SC-1", followups: true })?.type, "done");
});

test("only followups on screen that were not acknowledged yet are reported, per task", () => {
  const turns: ChatTurn[] = [
    { id: "a", role: "assistant", content: "x", status: "complete", taskId: "SC-1", awaiting: true, followups: [{ id: 7, text: "Anna replied" }, { id: 8, text: "again" }] },
    { id: "b", role: "assistant", content: "y", status: "complete", taskId: "SC-2", followups: [{ id: 9, text: "Bob replied" }] },
    { id: "c", role: "assistant", content: "z", status: "complete" },
  ];
  assert.deepEqual(unacknowledgedFollowups(turns, new Set([8])), [{ taskId: "SC-1", stepIds: [7] }, { taskId: "SC-2", stepIds: [9] }]);
  assert.deepEqual(unacknowledgedFollowups(turns, new Set([7, 8, 9])), []);
  assert.deepEqual(unacknowledgedFollowups([{ ...turns[0], taskId: undefined }], new Set()), []);
});
