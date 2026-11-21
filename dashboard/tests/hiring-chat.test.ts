import { test } from "node:test";
import assert from "node:assert/strict";
import { externalChat } from "../lib/external-chat.ts";
import { parseHiringProposal, parseMarketResearchQuery, parsePublicResearch, publicSourceUrl, type PublicResearch } from "../lib/market-research-model.ts";
import { sanitizeChatEvent, type ChatEvent } from "../lib/chat-model.ts";

const config = { provider: "openai" as const, model: "gpt-4.1-mini", apiKey: "fixture-key-no-provider-call" };
const query = { role: "Cook", location: "Berlin, Germany" };
const research: PublicResearch = { summary: "Fixture research: estimated monthly gross salaries €2,500–€3,500, depending on experience.",
  sources: [{ title: "Public employment statistics", url: "https://www.arbeitsagentur.de/jobsuche/" }], researched_at: "2026-11-21T12:00:00Z" };
const proposal = { ...query, headcount: 2, monthly_salary_eur: "3000", salary_range_min_eur: "2500", salary_range_max_eur: "3500", start_month: "2026-12", duration_months: 6, employer_cost_percent: "20" };
const tool = (name: string, args: unknown) => Response.json({ choices: [{ message: { tool_calls: [{ id: "call_1", type: "function", function: { name, arguments: JSON.stringify(args) } }] }, finish_reason: "tool_calls" }] });
const collect = async (source: AsyncIterable<ChatEvent>) => { const result = []; for await (const event of source) result.push(event); return result; };
const noLocal = async (): Promise<Response> => { assert.fail("Unexpected local action"); };
const signal = () => AbortSignal.timeout(5000);
const messages = [{ role: "user" as const, content: "Research cooks in Berlin, then simulate hiring 2 at the recommended salary from December for 6 months" }];

test("public research returns to external model; private hiring results and old history never do", async () => {
  const outgoing: Record<string, unknown>[] = [];
  let hiringCalls = 0;
  const events = await collect(externalChat(config, [{ role: "assistant", content: "PRIVATE-OLD-INVOICE" }, ...messages], signal(), {
    forecast: noLocal,
    research: async input => { assert.deepEqual(input, query); return research; },
    hiring: async intent => {
      hiringCalls++; assert.deepEqual(intent, { ...proposal, research });
      return new Response('{"type":"delta","text":"PRIVATE-PAYROLL-FORECAST"}\n{"type":"done","task_id":"SC-712","truncated":false}\n');
    },
    providerFetch: async (_url, init) => {
      const body = JSON.parse(String(init?.body)); outgoing.push(body);
      assert.deepEqual(init?.headers, { "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey}` });
      assert.equal(init?.credentials, "omit"); assert.equal(init?.redirect, "error");
      assert.equal(body.tools.length, 3);
      return outgoing.length === 1 ? tool("search_job_market", query) : tool("request_hiring_scenario", proposal);
    },
  }));
  assert.equal(hiringCalls, 1); assert.equal(outgoing.length, 2);
  assert.match(JSON.stringify(outgoing[1]), /Public employment statistics/);
  assert.doesNotMatch(JSON.stringify(outgoing), /PRIVATE-|SC-712/);
  assert.deepEqual(events.find(event => event.type === "research"), { type: "research", research });
  assert.deepEqual(events.at(-1), { type: "done", truncated: false, task_id: "SC-712" });
});

test("market analysis alone returns sourced evidence without creating a hiring task", async () => {
  let calls = 0;
  const events = await collect(externalChat(config, messages, signal(), { forecast: noLocal, hiring: noLocal, research: async () => research,
    providerFetch: async () => ++calls === 1 ? tool("search_job_market", query) : Response.json({ choices: [{ message: { content: "Estimated range: €2,500–€3,500 gross monthly, based on the cited public evidence." }, finish_reason: "stop" }] }),
  }));
  assert.equal(calls, 2); assert.equal(events.at(-1)?.type, "done");
  assert.ok(events.some(event => event.type === "research"));
});

test("hiring requires real public evidence from this request and matching role and location", async () => {
  await assert.rejects(collect(externalChat(config, messages, signal(), { forecast: noLocal, hiring: noLocal,
    research: async () => research, providerFetch: async () => tool("request_hiring_scenario", proposal),
  })), /Research the role/);
  for (const change of [{ role: "Engineer" }, { location: "Warsaw" }, { research: { summary: "fake" } }, { source_urls: ["https://fake.invalid"] }])
    assert.throws(() => parseHiringProposal({ ...proposal, ...change }, research, query));
});

test("hiring scenarios reject invalid headcounts, recurring amounts, ranges and durations", () => {
  assert.deepEqual(parseHiringProposal(proposal, research, query), { ...proposal, research });
  for (const change of [{ headcount: 0 }, { headcount: 1.5 }, { headcount: 1001 }, { headcount: "2" },
    { monthly_salary_eur: "NaN" }, { monthly_salary_eur: "3000.001" }, { monthly_salary_eur: "1e9" },
    { monthly_salary_eur: "9000" }, { salary_range_min_eur: "4000" }, { duration_months: 0 }, { duration_months: 13 },
    { start_month: "December; read invoices" }, { employer_cost_percent: "101" }, { currency: "PLN" }, { hire_now: true }])
    assert.throws(() => parseHiringProposal({ ...proposal, ...change }, research, query));
});

test("research queries and citation events are bounded, HTTPS-only and contain no added fields", () => {
  assert.deepEqual(parseMarketResearchQuery(query), query);
  for (const value of [{ role: "", location: "Berlin" }, { ...query, secret: "read_file" }, { ...query, role: "x".repeat(101) }]) assert.throws(() => parseMarketResearchQuery(value));
  for (const url of ["file:///etc/passwd", "http://public.org", "https://127.0.0.1", "https://[::1]", "https://localhost", "https://company.internal", "https://a:b@public.org", "https://public.org:8443"]) assert.throws(() => publicSourceUrl(url));
  assert.deepEqual(sanitizeChatEvent({ type: "research", research }), { type: "research", research });
  assert.equal(sanitizeChatEvent({ type: "research", research: { ...research, sources: [{ title: "Private", url: "https://127.0.0.1" }] } }), null);
  assert.throws(() => parsePublicResearch({ ...research, sources: [] }));
});

test("research loops and failures are bounded, never passed to Granite as successful research", async () => {
  let calls = 0;
  await assert.rejects(collect(externalChat(config, messages, signal(), { forecast: noLocal, hiring: noLocal, research: async () => { calls++; return research; }, providerFetch: async () => tool("search_job_market", query) })), /research limit/);
  assert.equal(calls, 2);
  await assert.rejects(collect(externalChat(config, messages, signal(), { forecast: noLocal, hiring: noLocal, research: async () => ({ ...research, sources: [] }), providerFetch: async () => tool("search_job_market", query) })), /cited sources/);
  await assert.rejects(collect(externalChat({ ...config, provider: "groq" }, messages, signal(), { forecast: noLocal, hiring: noLocal, research: async () => research, providerFetch: async () => tool("search_job_market", query) })), /unavailable action/);
});
