import { test, after } from "node:test";
import assert from "node:assert/strict";
import { parseStrategyPreferences } from "../lib/strategy-model.ts";
import { sealStrategyProvider, openStrategyProvider } from "../lib/strategy-credentials.ts";
import { researchPublicNews } from "../lib/market-research.ts";
import { defaultStrategyProvider } from "../lib/strategy-store.ts";
const oldSecret = process.env.SCOUT_STRATEGY_SECRET, oldKey = process.env.API_KEY;
after(() => { if (oldSecret === undefined) delete process.env.SCOUT_STRATEGY_SECRET; else process.env.SCOUT_STRATEGY_SECRET = oldSecret;
  if (oldKey === undefined) delete process.env.API_KEY; else process.env.API_KEY = oldKey; });
const config = { provider: "openai" as const, model: "gpt-4.1-mini", apiKey: "fixture-key-never-real" };
test("saved keys are randomized, authenticated and bound to the workspace", () => {
  process.env.SCOUT_STRATEGY_SECRET = "fixture-encryption-secret-at-least-32-characters";
  const a = sealStrategyProvider(config, "workspace-a"), b = sealStrategyProvider(config, "workspace-a");
  assert.notEqual(a, b); assert.ok(!a.includes(config.apiKey));
  assert.deepEqual(openStrategyProvider(a, "workspace-a"), config);
  assert.throws(() => openStrategyProvider(a, "workspace-b"));
  const parts = a.split("."); const bytes = Buffer.from(parts[2], "base64url"); bytes[0] ^= 1; parts[2] = bytes.toString("base64url");
  assert.throws(() => openStrategyProvider(parts.join("."), "workspace-a"));
});
test("the configured API_KEY automatically supplies GPT, and provides a stable encryption fallback", () => {
  delete process.env.SCOUT_STRATEGY_SECRET; process.env.API_KEY = "fixture-gpt-key-longer-than-32-characters";
  assert.equal(defaultStrategyProvider()?.apiKey, process.env.API_KEY);
  assert.equal(defaultStrategyProvider()?.provider, "openai");
  assert.deepEqual(openStrategyProvider(sealStrategyProvider(config, "workspace"), "workspace"), config);
  delete process.env.API_KEY; assert.equal(defaultStrategyProvider(), null);
  assert.throws(() => sealStrategyProvider(config, "workspace"), /not configured/);
});
test("strategy preferences require bounded company context, prompt and explicit enabled state", () => {
  const valid = { enabled: true, companyContext: "Local restaurant in Poland", systemPrompt: "Watch food prices and regulations" };
  assert.deepEqual(parseStrategyPreferences(valid), valid);
  for (const value of [null, {}, { ...valid, enabled: "yes" }, { ...valid, companyContext: " " }, { ...valid, systemPrompt: "x".repeat(8001) }])
    assert.throws(() => parseStrategyPreferences(value));
});
test("news sends the selected system prompt through native web search and saves only cited evidence", async () => {
  const result = await researchPublicNews(config, "Watch food prices and local regulations", "Public company context: Polish restaurant. Last 24 hours.", AbortSignal.timeout(5000), async (url, init) => {
    assert.equal(url, "https://api.openai.com/v1/responses");
    const body = JSON.parse(String(init?.body)); assert.match(body.instructions, /Watch food prices/); assert.match(body.input, /Last 24 hours/);
    assert.deepEqual(body.tools, [{ type: "web_search" }]); assert.equal(body.tool_choice, "required");
    assert.equal(body.store, false); assert.equal(init?.credentials, "omit");
    assert.doesNotMatch(JSON.stringify(body), /fixture-key|transactions|invoice/);
    return Response.json({ status: "completed", output: [ { type: "web_search_call", status: "completed", action: { type: "search" } },
      { type: "message", role: "assistant", content: [{ type: "output_text", text: "Food prices changed today. Check supplier quotes.", annotations: [{ type: "url_citation", title: "Statistics", url: "https://stat.gov.pl/" }] }] } ] });
  });
  assert.equal(result.sources[0].url, "https://stat.gov.pl/");
  await assert.rejects(researchPublicNews({ ...config, provider: "groq" }, "prompt", "context", AbortSignal.timeout(5000), async () => assert.fail()), /requires OpenAI or Mistral/);
});
