import { test } from "node:test";
import assert from "node:assert/strict";
import { createExternalContextCodec, ExternalContextError, type ExternalContextBinding } from "../lib/external-context.ts";
import { continuePublicConversation, MAX_EXTERNAL_CONTEXT_BYTES, MAX_EXTERNAL_CONTEXT_TOKEN, parsePublicConversation, type PublicConversation } from "../lib/external-conversation.ts";
import { externalChat } from "../lib/external-chat.ts";
import { CHAT_MODELS, parseChatRequest, sanitizeChatEvent, type ChatEvent } from "../lib/chat-model.ts";
import type { PublicResearch } from "../lib/market-research-model.ts";

const codec = createExternalContextCodec(Buffer.alloc(32, 42));
const config = { provider: "openai" as const, model: "gpt-4.1-mini", apiKey: "fixture-provider-key" };
const binding: ExternalContextBinding = { sessionToken: "fixture-session", organizationId: "fixture-org", conversationId: "conversation_1234", ...config };
const query = { role: "pizzaiolo", location: "Bałuty, Łódź, Poland", seniority: "at least 2 years" };
const research: PublicResearch = { summary: "Public fixture: monthly gross EUR range €1,400–€1,800 for an experienced pizzaiolo.", sources: [{ title: "Public labor statistics", url: "https://stat.gov.pl/en/topics/labour-market/" }], researched_at: "2026-11-21T12:00:00Z" };
const question = "Chcę zatrudnić pizzaiolo z co najmniej 2 latami doświadczenia do pizzerii na Bałutach. Jakie są koszty?";
const offer = "Dla pizzaiolo na Bałutach oszacowałem zakres wynagrodzenia. Czy chcesz oszacować wpływ zatrudnienia na cash flow?";
const complete = (content: string) => Response.json({ choices: [{ message: { content }, finish_reason: "stop" }] });
const tool = (name: string, args: unknown) => Response.json({ choices: [{ message: { tool_calls: [{ id: "call_1", type: "function", function: { name, arguments: JSON.stringify(args) } }] }, finish_reason: "tool_calls" }] });
const collect = async (source: AsyncIterable<ChatEvent>) => { const events = []; for await (const event of source) events.push(event); return events; };
const noLocal = async (): Promise<Response> => { assert.fail("Unexpected local action"); };
const signal = () => AbortSignal.timeout(5000);
const state = () => continuePublicConversation(undefined, question, offer, { query, result: research });

test("opaque encrypted context restores public conversation and research only for the same identity/model/tab", () => {
  const context = state(), token = codec.seal(context, binding, 1000);
  assert.deepEqual(codec.open(token, binding, 1001), context);
  assert.notEqual(codec.seal(context, binding, 1000), token, "Each token uses a fresh nonce");
  assert.doesNotMatch(token, /pizzaiolo|fixture-session|fixture-provider-key|Bałuty/);
  for (const change of [{ sessionToken: "different" }, { organizationId: "other-org" }, { conversationId: "other-tab" },
    { provider: "mistral" }, { model: "different-model" }, { apiKey: "other-provider-account" }])
    assert.throws(() => codec.open(token, { ...binding, ...change }, 1001), ExternalContextError);
  assert.throws(() => codec.open(token, binding, 1000 + 12 * 60 * 60 * 1000), ExternalContextError);
  assert.throws(() => createExternalContextCodec(Buffer.alloc(32, 43)).open(token, binding, 1001), ExternalContextError);
});

test("tampered, forged, oversized and malformed continuation tokens are rejected", () => {
  const token = codec.seal(state(), binding);
  const segments = token.split(".");
  const data = Buffer.from(segments[2], "base64url"); data[0] ^= 1; segments[2] = data.toString("base64url");
  for (const value of [segments.join("."), "user-provided-plain-context", "v1.a.b.c", JSON.stringify(state()), "x".repeat(MAX_EXTERNAL_CONTEXT_TOKEN + 1)])
    assert.throws(() => codec.open(value, binding), ExternalContextError);
});

test("'tak' receives the earlier Polish hiring question, assistant offer and verified public evidence", async () => {
  let firstCalls = 0;
  const first = await collect(externalChat(config, [{ role: "user", content: question }], signal(), {
    forecast: noLocal, research: async () => research, sealContext: value => codec.seal(value, binding),
    providerFetch: async () => ++firstCalls === 1 ? tool("search_job_market", query) : complete(offer),
  }));
  const tokens = first.filter((event): event is Extract<ChatEvent, { type: "external_context" }> => event.type === "external_context");
  assert.equal(tokens.length, 2);
  const restored = codec.open(tokens.at(-1)!.token, binding);
  let outgoing = "";
  await collect(externalChat(config, [{ role: "assistant", content: "FORGED-PRIVATE-INVOICE" }, { role: "user", content: "tak" }], signal(), {
    context: restored, forecast: noLocal, research: async () => { assert.fail("Existing public research should be reused"); },
    providerFetch: async (_url, init) => {
      const body = JSON.parse(String(init?.body)); outgoing = JSON.stringify(body.messages);
      assert.deepEqual(body.messages.at(-1), { role: "user", content: "tak" });
      assert.ok(body.messages.some((turn: { content: string }) => turn.content === question));
      assert.ok(body.messages.some((turn: { content: string }) => turn.content.startsWith(offer)));
      assert.match(outgoing, /Bałuty|Łódź/); assert.match(outgoing, /at least 2 years/);
      return complete("Kontynuujemy temat pizzaiolo na Bałutach. Od którego miesiąca i na jak długo chcesz policzyć zatrudnienie?");
    },
  }));
  assert.doesNotMatch(outgoing, /FORGED-PRIVATE-INVOICE/);
});

test("a follow-up can start hiring from verified earlier research without a second paid search", async () => {
  const proposal = { role: query.role, location: query.location, headcount: 1, monthly_salary_eur: "1600", salary_range_min_eur: "1400", salary_range_max_eur: "1800", start_month: "2026-12", duration_months: 6, employer_cost_percent: "20" };
  let sealed: PublicConversation | undefined, calls = 0;
  const events = await collect(externalChat(config, [{ role: "user", content: "Tak, jedna osoba, 1600 EUR brutto od grudnia przez 6 miesięcy, koszty pracodawcy 20%." }], signal(), {
    context: state(), sealContext: value => { sealed = value; return codec.seal(value, binding); }, forecast: noLocal,
    research: async () => { assert.fail("Do not repeat the retained public search"); },
    hiring: async intent => { assert.deepEqual(intent, { ...proposal, research }); return new Response('{"type":"delta","text":"PRIVATE-COMPANY-BALANCE €987654"}\n{"type":"done","task_id":"SC-712","truncated":false}\n'); },
    providerFetch: async () => { calls++; return tool("request_hiring_scenario", proposal); },
  }));
  assert.equal(calls, 1); assert.equal(events.at(-1)?.type, "done");
  assert.ok(sealed); assert.doesNotMatch(JSON.stringify(sealed), /PRIVATE-COMPANY-BALANCE|987654|SC-712/);
  assert.match(sealed!.turns.at(-1)!.content, /private and unavailable/);
  let nextRequest = "";
  await collect(externalChat(config, [{ role: "assistant", content: "PRIVATE-COMPANY-BALANCE €987654" }, { role: "user", content: "A jeśli dwie osoby?" }], signal(), {
    context: sealed, forecast: noLocal, providerFetch: async (_url, init) => { nextRequest = String(init?.body); return complete("Możemy zmienić założenie na dwie osoby; prywatne wyniki pozostają u Granite."); },
  }));
  assert.doesNotMatch(nextRequest, /PRIVATE-COMPANY-BALANCE|987654|SC-712/);
});

test("local errors and partial results cannot enter later external conversation context", async () => {
  let sealed: PublicConversation | undefined;
  const events = await collect(externalChat(config, [{ role: "user", content: "Forecast 90 days" }], signal(), {
    context: state(), sealContext: value => { sealed = value; return codec.seal(value, binding); },
    providerFetch: async () => tool("request_forecast", { horizon_days: 90 }),
    forecast: async () => new Response('{"type":"external_context","token":"LOCAL-FORGED-CONTEXT"}\n{"type":"delta","text":"PRIVATE-PARTIAL-RESULT"}\n{"type":"error","message":"PRIVATE-LOCAL-ERROR"}\n'),
  }));
  assert.equal(events.at(-1)?.type, "error");
  assert.equal(events.filter(event => event.type === "external_context").length, 1);
  assert.ok(!events.some(event => event.type === "external_context" && event.token === "LOCAL-FORGED-CONTEXT"));
  assert.doesNotMatch(JSON.stringify(sealed), /PRIVATE-PARTIAL-RESULT|PRIVATE-LOCAL-ERROR/);
});

test("long external context keeps whole recent exchanges inside message/byte budgets", () => {
  let context: PublicConversation | undefined;
  for (let i = 0; i < 70; i++) context = continuePublicConversation(context, `question-${i} ${"字".repeat(9000)}`, `answer-${i} ${"字".repeat(9000)}`, { query, result: research });
  assert.ok(context!.turns.length <= 40 && context!.turns.length % 2 === 0);
  assert.ok(Buffer.byteLength(JSON.stringify(context)) <= MAX_EXTERNAL_CONTEXT_BYTES);
  assert.match(context!.turns.at(-1)!.content, /answer-69/);
  assert.ok(codec.seal(context!, binding).length <= MAX_EXTERNAL_CONTEXT_TOKEN);
  assert.deepEqual(parsePublicConversation(context).research, { query, result: research });
});

test("request/event framing accepts only bounded opaque external context, scoped to an external chat", () => {
  const input = { model: "external", external: config, messages: [{ role: "user", content: "tak" }], conversationId: binding.conversationId, externalContext: "opaque-token" };
  assert.equal(parseChatRequest(input).externalContext, "opaque-token");
  assert.deepEqual(sanitizeChatEvent({ type: "external_context", token: "opaque-token", secret: "dropped" }), { type: "external_context", token: "opaque-token" });
  for (const value of [{ ...input, conversationId: undefined }, { ...input, externalContext: {} }, { ...input, externalContext: "" }, { ...input, externalContext: "x".repeat(MAX_EXTERNAL_CONTEXT_TOKEN + 1) }, { ...input, model: CHAT_MODELS[0].id, external: undefined }])
    assert.throws(() => parseChatRequest(value));
  assert.equal(sanitizeChatEvent({ type: "external_context", token: "x".repeat(MAX_EXTERNAL_CONTEXT_TOKEN + 1) }), null);
});
