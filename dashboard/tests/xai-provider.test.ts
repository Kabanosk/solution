import { test } from "node:test";
import assert from "node:assert/strict";
import { EXTERNAL_PROVIDERS, parseExternalConfig, type ExternalConfig } from "../lib/external-model.ts";
import { parseChatRequest } from "../lib/chat-model.ts";
import { researchJobMarket, researchPublicNews, supportsMarketResearch } from "../lib/market-research.ts";

const xai: ExternalConfig = { provider: "xai", model: "grok-4.7", apiKey: "xai-test-123456" };
const messages = [{ role: "user" as const, content: "Hello" }];
const noCall = async (): Promise<Response> => assert.fail("No outbound request for xAI research");

test("xAI is a provider with its fixed endpoint and default model, and is accepted in a chat request", () => {
  const entry = EXTERNAL_PROVIDERS.find(provider => provider.id === "xai");
  assert.deepEqual(entry && { name: entry.name, endpoint: entry.endpoint, model: entry.model },
    { name: "xAI", endpoint: "https://api.x.ai/v1/chat/completions", model: "grok-4.7" });
  assert.deepEqual(parseExternalConfig(xai), xai);
  assert.deepEqual(parseChatRequest({ model: "external", external: xai, messages }).external, xai);
  assert.deepEqual(EXTERNAL_PROVIDERS.map(provider => provider.id), ["openai", "mistral", "groq", "xai"]);
});

test("xAI cannot do public web research: a clear error without an outbound request", async () => {
  assert.equal(supportsMarketResearch("xai"), false);
  await assert.rejects(researchJobMarket(xai, { role: "Engineer", location: "Warsaw", seniority: "Mid" }, AbortSignal.timeout(5000), noCall), /OpenAI or Mistral/);
  await assert.rejects(researchPublicNews(xai, "prompt", "context", AbortSignal.timeout(5000), noCall), /requires OpenAI or Mistral/);
});

test("an xAI key in the environment becomes the chat's server-side provider; without it there is none", async () => {
  const { defaultChatProvider } = await import("../lib/strategy-store.ts");
  const oldKey = process.env.XAI_API_KEY, oldModel = process.env.SCOUT_XAI_MODEL;
  try {
    delete process.env.XAI_API_KEY; delete process.env.SCOUT_XAI_MODEL;
    assert.equal(defaultChatProvider(), null);
    process.env.XAI_API_KEY = xai.apiKey;
    assert.deepEqual(defaultChatProvider(), xai);
    process.env.SCOUT_XAI_MODEL = "grok-4.3";
    assert.equal(defaultChatProvider()?.model, "grok-4.3");
  } finally {
    if (oldKey === undefined) delete process.env.XAI_API_KEY; else process.env.XAI_API_KEY = oldKey;
    if (oldModel === undefined) delete process.env.SCOUT_XAI_MODEL; else process.env.SCOUT_XAI_MODEL = oldModel;
  }
});
