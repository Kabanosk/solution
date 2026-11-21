import { test } from "node:test";
import assert from "node:assert/strict";
import { researchJobMarket, supportsMarketResearch, MarketResearchError } from "../lib/market-research.ts";
import type { ExternalConfig } from "../lib/external-model.ts";

const config: ExternalConfig = { provider: "openai", model: "gpt-4.1-mini", apiKey: "research-test-key" };
const query = { role: "Software engineer", location: "Warsaw, Poland", seniority: "Mid-level" };
const citation = { type: "url_citation", title: "Public employer salary posting", url: "https://careers.company.org/jobs/engineer" };
const signal = () => AbortSignal.timeout(5000);
const openai = (annotations: unknown[] = [citation]) => ({ status: "completed", output: [
  { type: "web_search_call", status: "completed", action: { type: "search", query: "public software salary Warsaw" } },
  { type: "message", role: "assistant", content: [{ type: "output_text", text: "Monthly gross EUR estimate: 3000–4500. Confidence: medium; employer contributions excluded.", annotations }] },
] });
const mistral = () => ({ outputs: [
  { type: "tool.execution", name: "web_search", completed_at: "2026-11-21T12:00:00Z" },
  { type: "message.output", role: "assistant", content: [
    { type: "text", text: "Current postings suggest €3000–4500 gross per month; estimate, employer contributions excluded." },
    { type: "tool_reference", tool: "web_search", title: citation.title, url: citation.url },
  ] },
] });
const response = (body: unknown): typeof fetch => async () => Response.json(body);

test("OpenAI uses native web search with only bounded public job criteria and selected-provider auth", async () => {
  let calls = 0;
  const result = await researchJobMarket(config, query, signal(), async (url, init) => {
    calls++;
    assert.equal(url, "https://api.openai.com/v1/responses");
    assert.deepEqual(init?.headers, { "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey}` });
    assert.equal(init?.credentials, "omit"); assert.equal(init?.redirect, "error"); assert.equal(init?.cache, "no-store");
    assert.ok(init?.signal);
    const body = JSON.parse(String(init?.body));
    assert.equal(body.model, config.model); assert.equal(body.store, false); assert.equal(body.stream, false);
    assert.deepEqual(body.tools, [{ type: "web_search" }]); assert.equal(body.tool_choice, "required");
    assert.equal(body.max_tool_calls, 4);
    assert.match(body.input, /Software engineer/); assert.match(body.input, /Warsaw, Poland/);
    assert.match(body.instructions, /never invent an exchange rate/);
    assert.doesNotMatch(JSON.stringify(body), /research-test-key|conversation_id|X-Scout-Organization/);
    return Response.json(openai());
  });
  assert.equal(calls, 1);
  assert.deepEqual(result.sources, [{ title: citation.title, url: citation.url }]);
  assert.match(result.summary, /\[Public employer salary posting\]\(https:\/\/careers.company.org\/jobs\/engineer\)/);
  assert.ok(Number.isFinite(Date.parse(result.researched_at)));
});

test("OpenAI citation offsets become clickable inline references", async () => {
  const body = openai([{ ...citation, start_index: 9, end_index: 17 }]);
  body.output[1] = { type: "message", role: "assistant", content: [{ type: "output_text", text: "Evidence [source] supports this estimate.", annotations: [{ ...citation, start_index: 9, end_index: 17 }] }] };
  const result = await researchJobMarket(config, query, signal(), response(body));
  assert.equal(result.summary, "Evidence [Public employer salary posting](https://careers.company.org/jobs/engineer) supports this estimate.");
});

test("Mistral uses a stateless native conversation with interleaved web-search citations", async () => {
  const result = await researchJobMarket({ ...config, provider: "mistral", model: "mistral-small-latest" }, query, signal(), async (url, init) => {
    assert.equal(url, "https://api.mistral.ai/v1/conversations");
    const body = JSON.parse(String(init?.body));
    assert.equal(body.model, "mistral-small-latest"); assert.equal(body.store, false); assert.equal(body.stream, false);
    assert.deepEqual(body.tools, [{ type: "web_search" }]); assert.equal(body.agent_id, undefined);
    assert.match(body.inputs, /Mid-level/); assert.ok(body.completion_args.max_tokens <= 2000);
    return Response.json(mistral());
  });
  assert.deepEqual(result.sources, [{ title: citation.title, url: citation.url }]);
  assert.match(result.summary, /Current postings/); assert.match(result.summary, /\[Public employer salary posting\]/);
});

test("retired Groq web search fails clearly without an outbound request", async () => {
  assert.equal(supportsMarketResearch("groq"), false);
  assert.equal(supportsMarketResearch("openai"), true); assert.equal(supportsMarketResearch("mistral"), true);
  await assert.rejects(researchJobMarket({ ...config, provider: "groq" }, query, signal(), async () => assert.fail("No retired API calls")), /retired/);
});

test("model-memory responses or guessed URLs are never accepted as live research", async () => {
  const missingSearch = openai(); missingSearch.output.shift();
  const missingCitations = openai([]);
  const incomplete = { ...openai(), status: "incomplete" };
  const pending = openai(); pending.output[0].status = "in_progress";
  for (const body of [missingSearch, missingCitations, incomplete, pending,
    { status: "completed", output: [{ type: "message", role: "assistant", content: [{ type: "output_text", text: "I estimate €5000. https://a-guessed-source.org" }] }] }])
    await assert.rejects(researchJobMarket(config, query, signal(), response(body)), MarketResearchError);
  const nativeMissingSearch = mistral(); nativeMissingSearch.outputs.shift();
  const nativeMissingCitations = mistral(); nativeMissingCitations.outputs[1].content = [{ type: "text", text: "An uncited guess." }];
  for (const body of [nativeMissingSearch, nativeMissingCitations])
    await assert.rejects(researchJobMarket({ ...config, provider: "mistral" }, query, signal(), response(body)), MarketResearchError);
});

test("private, credential-bearing, non-HTTPS and malicious citation destinations are rejected", async () => {
  for (const url of ["http://public.org/job", "https://127.0.0.1/job", "https://10.0.0.1", "https://localhost/job",
    "https://api.cluster.internal/job", "https://[::1]/job", "https://user:pass@public.org", "javascript:alert(1)", "https://public.org:8443/job"]) {
    await assert.rejects(researchJobMarket(config, query, signal(), response(openai([{ ...citation, url }]))), MarketResearchError);
  }
  const result = await researchJobMarket(config, query, signal(), response(openai([{ ...citation, url: "https://127.0.0.1" }, citation, citation])));
  assert.equal(result.sources.length, 1); assert.equal(result.sources[0].url, citation.url);
});

test("native citations are bounded and deduplicated rather than arbitrary model-generated source lists", async () => {
  const body = { ...openai(Array.from({ length: 20 }, (_, index) => ({ ...citation, url: `https://careers.company.org/jobs/${index}` }))), sources: [{ title: "Invented", url: "https://guessed.org" }] };
  const result = await researchJobMarket(config, query, signal(), response(body));
  assert.equal(result.sources.length, 8); assert.ok(result.sources.every(source => source.url.startsWith("https://careers.company.org/")));
});

test("invalid inputs do not enter a provider request", async () => {
  const noRequest: typeof fetch = async () => assert.fail("Invalid inputs must not be sent");
  for (const invalid of [{ role: "", location: "Warsaw" }, { role: "x".repeat(101), location: "Warsaw" },
    { ...query, company_invoices: "PRIVATE" }, { role: "Engineer", location: "Warsaw\u0000" }])
    await assert.rejects(researchJobMarket(config, invalid as typeof query, signal(), noRequest), MarketResearchError);
});

test("oversized, malformed, empty or incomplete provider responses fail safely", async () => {
  for (const body of [() => new Response("x".repeat(524289)), () => new Response("not JSON"), () => new Response(null),
    () => Response.json({ ...openai(), output: [] }), () => Response.json(openai([{ ...citation, title: "x".repeat(201) }]))])
    await assert.rejects(researchJobMarket(config, query, signal(), async () => body()), MarketResearchError);
});

test("provider/network failures never reflect response bodies, credentials or raw errors", async () => {
  for (const status of [400, 401, 403, 429, 500])
    await assert.rejects(researchJobMarket(config, query, signal(), async () => new Response(`INTERNAL ERROR ${config.apiKey}`, { status })),
      error => error instanceof MarketResearchError && !error.message.includes(config.apiKey) && !error.message.includes("INTERNAL ERROR"));
  await assert.rejects(researchJobMarket(config, query, signal(), async () => { throw new Error(`secret ${config.apiKey}`); }), /Could not reach/);
});
