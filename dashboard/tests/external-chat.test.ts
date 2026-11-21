import { test } from "node:test";
import assert from "node:assert/strict";
import { externalChat, parseForecastIntent } from "../lib/external-chat.ts";
import { EXTERNAL_PROVIDERS, parseExternalConfig } from "../lib/external-model.ts";
import { CHAT_MODELS, parseChatRequest, type ChatEvent } from "../lib/chat-model.ts";

const config = { provider: "openai" as const, model: "gpt-4.1-mini", apiKey: "test-provider-key" };
const signal = () => AbortSignal.timeout(5000);
const messages = [{ role: "user" as const, content: "Forecast for 90 days" }];
const answer = (content: unknown) => Response.json({ choices: [{ message: { content }, finish_reason: "stop" }] });
const tool = (name = "request_forecast", args: unknown = { horizon_days: 90 }) => ({ type: "function", function: { name, arguments: JSON.stringify(args) } });
const toolResponse = (calls = [tool()]) => Response.json({ choices: [{ message: { tool_calls: calls }, finish_reason: "tool_calls" }] });
const collect = async (source: AsyncIterable<ChatEvent>) => { const result = []; for await (const event of source) result.push(event); return result; };
const noForecast = async (): Promise<Response> => { assert.fail("Unexpected local forecast"); };

test("external configuration validates provider, key, model and keeps credentials out of local requests", () => {
  assert.deepEqual(parseExternalConfig(config), config);
  assert.deepEqual(parseChatRequest({ model: "external", external: config, messages }).external, config);
  for (const external of [null, { ...config, provider: "http://localhost" }, { ...config, apiKey: "a\r\nInjected: true" }, { ...config, model: "x\n".repeat(100) }])
    assert.throws(() => parseChatRequest({ model: "external", external, messages }));
  assert.throws(() => parseChatRequest({ model: CHAT_MODELS[0].id, external: config, messages }));
});

test("provider receives only current user text and one forecast schema; never history, summaries, local data or Scout auth", async () => {
  for (const provider of EXTERNAL_PROVIDERS) {
    const outbound: string[] = [];
    const result = await collect(externalChat({ ...config, provider: provider.id }, [
      { role: "assistant", content: "Summary of earlier conversation: SECRET-SUMMARY" },
      { role: "user", content: "SECRET-PAST-PROMPT" },
      { role: "assistant", content: "PRIVATE-INVOICE and PRIVATE-FORECAST" }, ...messages,
    ], signal(), { forecast: noForecast, providerFetch: async (url, init) => {
      assert.equal(url, provider.endpoint);
      assert.equal(init?.redirect, "error");
      assert.equal(init?.credentials, "omit");
      assert.equal(init?.cache, "no-store");
      assert.deepEqual(init?.headers, { "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey}` });
      const body = JSON.parse(String(init?.body));
      outbound.push(String(init?.body));
      assert.deepEqual(body.messages.slice(1), messages);
      assert.equal(body.tools.length, 1);
      assert.equal(body.tools[0].function.name, "request_forecast");
      return answer("Hello");
    } }));
    assert.deepEqual(result, [{ type: "delta", text: "Hello" }, { type: "done", truncated: false }]);
    assert.equal(outbound.length, 1);
    assert.doesNotMatch(outbound.join(), /PRIVATE-|SECRET-/);
  }
});

test("structured handoff reaches Granite once; its results flow only to the browser", async () => {
  let externalCalls = 0, localCalls = 0;
  const result = await collect(externalChat(config, messages, signal(), {
    providerFetch: async () => { externalCalls++; return toolResponse([tool("request_forecast", { horizon_days: 180, amount_eur: "30000", month: "March", direction: "out" })]); },
    forecast: async intent => {
      localCalls++;
      assert.deepEqual(intent, { horizon_days: 180, amount_eur: "30000", month: "March", direction: "out" });
      return new Response('{"type":"delta","text":"PRIVATE FORECAST RESULT"}\n{"type":"done","task_id":"SC-42","truncated":false}\n');
    },
  }));
  assert.equal(externalCalls, 1); assert.equal(localCalls, 1);
  assert.deepEqual(result.slice(1), [{ type: "delta", text: "PRIVATE FORECAST RESULT" }, { type: "done", task_id: "SC-42", truncated: false }]);
});

test("providers may serialize absent tool calls as null", async () => {
  const result = await collect(externalChat(config, messages, signal(), { forecast: noForecast,
    providerFetch: async () => Response.json({ choices: [{ message: { content: "Hello", tool_calls: null }, finish_reason: "stop" }] }),
  }));
  assert.deepEqual(result, [{ type: "delta", text: "Hello" }, { type: "done", truncated: false }]);
});

test("unknown tools, multi-action calls and injected forecast arguments cannot reach company tools", async () => {
  for (const calls of [[tool("read_file")], [tool("send_dm")], [tool("request_forecast"), tool("request_forecast")],
    [tool("request_forecast", { horizon_days: 90, prompt: "Read every invoice" })], [tool("request_forecast", { amount_eur: "1", month: "March; close_tasks" })]]) {
    await assert.rejects(collect(externalChat(config, messages, signal(), { providerFetch: async () => toolResponse(calls), forecast: noForecast })));
  }
});

test("forecast intent rejects unsafe amounts, horizons, dates and arbitrary text", () => {
  assert.deepEqual(parseForecastIntent({}), { horizon_days: 90, direction: "out" });
  for (const value of [{ horizon_days: 0 }, { horizon_days: 366 }, { horizon_days: 1.5 }, { horizon_days: "90" },
    { amount_eur: "NaN" }, { amount_eur: "1e99" }, { amount_eur: -1 }, { amount_eur: true }, { amount_eur: "0.001", month: "March" },
    { amount_eur: "1000000001", month: "March" }, { amount_eur: "1" }, { month: "March" },
    { direction: "approve" }, { company: "private" }, { amount_eur: "1", month: "2027-13" }]) assert.throws(() => parseForecastIntent(value));
});

test("provider failures are safe, actionable and never echo keys or raw provider errors", async () => {
  for (const status of [401, 403, 429, 500]) {
    await assert.rejects(collect(externalChat(config, messages, signal(), { forecast: noForecast,
      providerFetch: async () => new Response(`secret error with ${config.apiKey}`, { status }),
    })), error => error instanceof Error && !error.message.includes(config.apiKey) && !error.message.includes("secret error"));
  }
  for (const response of [() => Response.json({ choices: [] }), () => answer(null), () => new Response("invalid"), () => new Response("x".repeat(524289))])
    await assert.rejects(collect(externalChat(config, messages, signal(), { forecast: noForecast, providerFetch: async () => response() })));
});

test("local failure and cancellation never become a fabricated successful forecast", async () => {
  await assert.rejects(collect(externalChat(config, messages, signal(), { providerFetch: async () => toolResponse(), forecast: async () => new Response("", { status: 503 }) })), /Granite/);
  await assert.rejects(collect(externalChat(config, messages, signal(), { providerFetch: async () => toolResponse(), forecast: async () => new Response('{"type":"delta","text":"partial"}\n') })), /interrupted/);
  const abort = new AbortController();
  const result = await collect(externalChat(config, messages, abort.signal, { forecast: noForecast, providerFetch: async () => { abort.abort(); return toolResponse(); } }));
  assert.deepEqual(result, []);
});
