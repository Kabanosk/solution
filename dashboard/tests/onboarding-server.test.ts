import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseProvider, parseConnectBody, parseSetupUpdate, parseSlackChannels, redirectUri, providerCookieName, providerCookiePath,
  encodeProviderCookie, parseProviderCookie, validProviderAuthUrl, sanitizeSetup, mapBackendResult,
  startProviderConnect, completeProviderCallback, registrationRedirect, googleSignInRedirect, makeBackendFetch,
} from "../lib/onboarding-server.ts";

const ORIGIN = "https://scout.example";
const owner = { slug: "acme-studio", role: "owner" };
type Call = { path: string; init: RequestInit };
function fakeBackend(reply: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = [];
  return { calls, fetch: async (path: string, init: RequestInit = {}) => { const call = { path, init }; calls.push(call); return reply(call); } };
}
const jsonResponse = (body: unknown, status = 200) => Response.json(body, { status });
const setupWith = (id: string, state: string) => ({ version: 1, status: "in_progress", revision: 2, network: null, steps: [{ id, state, provider: id, message: null, display: {} }] });

test("Slack channel updates validate a bounded list rather than forwarding arbitrary config", () => {
  assert.deepEqual(parseSlackChannels({ channels: ["C12345678", "C12345678"], token: "DROP" }), { ok: true, value: { channels: ["C12345678"] } });
  assert.deepEqual(parseSlackChannels({ channels: [] }), { ok: true, value: { channels: [] } });
  for (const channels of [undefined, "C12345678", ["#general"], Array(21).fill("C12345678")]) assert.equal(parseSlackChannels({ channels }).ok, false);
});

test("a Slack grant awaiting channels is a successful authorization but not a connected service", async () => {
  const setup = setupWith("slack", "needs_configuration");
  setup.steps[0].display = { authorized: true };
  const backend = fakeBackend(() => jsonResponse(setup));
  const result = await completeProviderCallback({ provider: "slack", origin: ORIGIN,
    url: new URL(`${ORIGIN}/api/onboarding/callback/slack?state=s&code=c`),
    cookieValue: encodeProviderCookie(owner.slug, "proof"), session: owner, backend: backend.fetch });
  assert.equal(result.location, `${ORIGIN}/${owner.slug}?setup=slack-connected#setup`);
});

test("an existing grant gets a precise safe error instead of a stale-revision message", () => {
  const result = mapBackendResult(409, { error: { code: "already_connected", message: "PRIVATE PROVIDER DIAGNOSTICS" } });
  assert.equal((result.body as { code: string }).code, "already_connected");
  assert.match((result.body as { error: string }).error, /Disconnect/);
  assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
});

test("only the five supported providers are accepted; path tricks are not", () => {
  for (const p of ["bunq", "builtin", "ksef", "google", "slack"]) assert.equal(parseProvider(p), p);
  for (const p of ["Google", "../google", "google/x", "google?x=1", "", "revolut", undefined, 7, "__proto__"]) assert.equal(parseProvider(p), null);
});

test("connect bodies are rebuilt from per-provider allowlists", () => {
  assert.deepEqual(parseConnectBody("bunq", {}), { ok: true, value: {} });
  assert.deepEqual(parseConnectBody("bunq", { api_key: "  " }), { ok: true, value: {} });
  assert.deepEqual(parseConnectBody("bunq", { api_key: " sandbox_abc ", extra: "x" }), { ok: true, value: { api_key: "sandbox_abc" } });
  assert.equal(parseConnectBody("bunq", { api_key: "x".repeat(257) }).ok, false);
  assert.equal(parseConnectBody("bunq", { api_key: 5 }).ok, false);
  assert.deepEqual(parseConnectBody("builtin", { host: "evil" }), { ok: true, value: {} });
  assert.deepEqual(parseConnectBody("ksef", { nip: "1234567890", host: "evil" }), { ok: true, value: { nip: "1234567890" } });
  for (const nip of ["123", "12345678901", "abcdefghij", "", undefined, 1234567890]) assert.equal(parseConnectBody("ksef", { nip }).ok, false);
  // The browser never supplies the redirect URI or proof for OAuth providers.
  assert.deepEqual(parseConnectBody("google", { redirect_uri: "https://evil.example", browser_proof: "x", label: "Invoices" }), { ok: true, value: { label: "Invoices" } });
  assert.deepEqual(parseConnectBody("slack", {}), { ok: true, value: {} });
  assert.equal(parseConnectBody("google", { label: "x".repeat(101) }).ok, false);
  assert.equal(parseConnectBody("google", { label: "bad\nlabel" }).ok, false);
  assert.equal(parseConnectBody("google", { label: 3 }).ok, false);
});

test("setup updates validate action shape and rebuild the network answers", () => {
  const network = { required: true, provider: "WireGuard", protocol: "udp", systems: ["ERP", "Files"], administrator: "Ola IT", company_only: true };
  assert.deepEqual(parseSetupUpdate({ revision: 3, action: "skip" }), { ok: true, value: { revision: 3, action: "skip" } });
  assert.deepEqual(parseSetupUpdate({ revision: 0, action: "step", step: "bank", state: "skipped", junk: 1 }), { ok: true, value: { revision: 0, action: "step", step: "bank", state: "skipped" } });
  assert.deepEqual(parseSetupUpdate({ revision: 1, action: "network", network: { ...network, private_key: "-----BEGIN" } }), { ok: true, value: { revision: 1, action: "network", network } });
  for (const bad of [
    {}, { revision: -1, action: "skip" }, { revision: 1.5, action: "skip" }, { revision: "1", action: "skip" }, { revision: 1, action: "delete" },
    { revision: 1, action: "step" }, { revision: 1, action: "step", step: "vpn", state: "skipped" }, { revision: 1, action: "step", step: "bank", state: "connected" },
    { revision: 1, action: "network" }, { revision: 1, action: "network", network: { ...network, systems: Array(21).fill("x") } },
    { revision: 1, action: "network", network: { ...network, required: "yes" } }, { revision: 1, action: "network", network: { ...network, administrator: "a".repeat(201) } },
    { revision: 1, action: "complete", step: "bank" },
  ]) assert.equal(parseSetupUpdate(bad).ok, false, JSON.stringify(bad));
});

test("provider redirect URI and proof cookie are derived by the server only", () => {
  assert.equal(redirectUri(ORIGIN, "google"), "https://scout.example/api/onboarding/callback/google");
  assert.equal(providerCookieName("slack"), "scout_onboarding_slack");
  assert.notEqual(providerCookieName("google"), "scout_oauth");
  assert.equal(providerCookiePath("google"), "/api/onboarding/callback/google");
  assert.deepEqual(parseProviderCookie(encodeProviderCookie("acme-studio", "proof_-123")), { slug: "acme-studio", proof: "proof_-123" });
  for (const bad of [undefined, "", "acme-studio", "api.proof", "acme-studio.", ".proof", "acme-studio.pro of", "acme-studio.proof.more", "acme-studio." + "p".repeat(129)]) assert.equal(parseProviderCookie(bad), null, String(bad));
});

test("authorization URLs must be https on the provider's own host", () => {
  assert.equal(validProviderAuthUrl("google", "https://accounts.google.com/o/oauth2/v2/auth?client_id=1"), true);
  assert.equal(validProviderAuthUrl("slack", "https://slack.com/oauth/v2/authorize?client_id=1"), true);
  assert.equal(validProviderAuthUrl("slack", "https://acme.slack.com/oauth/v2/authorize"), true);
  for (const [p, u] of [["google", "http://accounts.google.com/x"], ["google", "https://accounts.google.com.evil.example/x"], ["google", "https://evil.example/?https://accounts.google.com"], ["slack", "https://slack.com.evil.example/"], ["google", "https://slack.com/x"], ["google", "javascript:alert(1)"], ["google", ""], ["google", 5]] as const) assert.equal(validProviderAuthUrl(p, u), false, `${p} ${u}`);
});

test("setup responses are rebuilt from the wire contract and drop unknown or secret fields", () => {
  const leaked = {
    version: 1, status: "in_progress", revision: 4, access_token: "ya29.SECRET", refresh_token: "1//SECRET",
    steps: [
      { id: "email", state: "connected", provider: "google", message: null, display: { mailbox: "ops@acme.example", label: "INBOX", connected: true, token: { nested: "SECRET" }, n: 4 }, secret: "SECRET" },
      { id: "vpn", state: "connected", provider: null, message: null, display: {} },
      { id: "bank", state: "teleported", provider: null, message: null, display: {} },
    ],
    network: { required: true, provider: "WireGuard", protocol: "udp", systems: ["ERP"], administrator: "IT", company_only: true, private_key: "SECRET" },
  };
  const clean = sanitizeSetup(leaked);
  assert.ok(clean);
  assert.equal(JSON.stringify(clean).includes("SECRET"), false);
  assert.deepEqual(clean.steps.map(s => [s.id, s.state]), [["email", "connected"]]);
  assert.deepEqual(clean.steps[0].display, { mailbox: "ops@acme.example", label: "INBOX", connected: true });
  assert.equal(clean.revision, 4);
  assert.equal(sanitizeSetup(null), null);
  assert.equal(sanitizeSetup({ version: 2, status: "pending", revision: 1, steps: [], network: null }), null);
  assert.equal(sanitizeSetup({ version: 1, status: "weird", revision: 1, steps: [], network: null }), null);
});

test("backend errors map to generic bodies; setup states pass through sanitized", () => {
  const detail = { detail: "psycopg error: password=hunter2 token=ya29.SECRET" };
  for (const [status, expected] of [[400, 400], [422, 400], [401, 401], [403, 403], [404, 404], [409, 409], [429, 429], [500, 502], [503, 502], [418, 502]] as const) {
    const result = mapBackendResult(status, detail);
    assert.equal(result.status, expected);
    assert.equal(JSON.stringify(result.body).includes("hunter2"), false);
    assert.equal(JSON.stringify(result.body).includes("SECRET"), false);
    assert.equal(typeof (result.body as { error: string }).error, "string");
  }
  assert.deepEqual(mapBackendResult(200, setupWith("bank", "sandbox_connected")), { status: 200, body: sanitizeSetup(setupWith("bank", "sandbox_connected")) });
  assert.equal(mapBackendResult(200, { unexpected: true }).status, 502);
  assert.equal(mapBackendResult(200, null).status, 502);
});

test("makeBackendFetch authenticates with the explicit session and refuses redirects and slow calls", async () => {
  const seen: { url: string; init: RequestInit }[] = [];
  const call = makeBackendFetch({ apiBase: "http://api:8000", token: "tok", slug: "acme-studio", fetchImpl: async (url, init) => { seen.push({ url: String(url), init: init ?? {} }); return jsonResponse({}); } });
  await call("/v1/onboarding/callback/google", { method: "POST", body: "{}" });
  const headers = new Headers(seen[0].init.headers);
  assert.equal(seen[0].url, "http://api:8000/v1/onboarding/callback/google");
  assert.equal(headers.get("authorization"), "Bearer tok");
  assert.equal(headers.get("x-scout-organization"), "acme-studio");
  assert.equal(seen[0].init.redirect, "error");
  assert.equal(seen[0].init.cache, "no-store");
  assert.ok(seen[0].init.signal);
});

test("starting OAuth derives the redirect, binds a proof cookie and never trusts browser values", async () => {
  const backend = fakeBackend(() => jsonResponse({ url: "https://accounts.google.com/o/oauth2/v2/auth?state=s" }));
  const result = await startProviderConnect({ provider: "google", body: { redirect_uri: "https://evil.example/cb", browser_proof: "attacker", label: "Invoices" }, origin: ORIGIN, session: owner, backend: backend.fetch, newProof: () => "fresh-proof" });
  assert.equal(result.status, 200);
  assert.deepEqual(result.body, { url: "https://accounts.google.com/o/oauth2/v2/auth?state=s" });
  assert.equal(backend.calls[0].path, "/v1/onboarding/connect/google");
  assert.equal(backend.calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(String(backend.calls[0].init.body)), { redirect_uri: "https://scout.example/api/onboarding/callback/google", browser_proof: "fresh-proof", label: "Invoices" });
  assert.deepEqual(result.cookie, { name: "scout_onboarding_google", value: "acme-studio.fresh-proof", path: "/api/onboarding/callback/google", maxAge: 600 });
});

test("unconfigured OAuth returns the safe prerequisite state without issuing a proof cookie", async () => {
  const setup = setupWith("email", "needs_configuration");
  const backend = fakeBackend(() => jsonResponse(setup));
  const result = await startProviderConnect({ provider: "google", body: {}, origin: ORIGIN, session: owner, backend: backend.fetch });
  assert.equal(result.status, 200);
  assert.deepEqual(result.body, setup);
  assert.equal(result.cookie, undefined);
});

test("string-valued credentials in display metadata are never serialized", () => {
  const raw = setupWith("email", "connected");
  raw.steps[0].display = { mailbox: "ops@acme.example", access_token: "LEAK-ME", refresh_token: "LEAK-ME", private_key: "LEAK-ME" };
  const clean = sanitizeSetup(raw);
  assert.equal(JSON.stringify(clean).includes("LEAK-ME"), false);
  assert.equal(clean?.steps[0].display.mailbox, "ops@acme.example");
});

test("starting OAuth refuses non-owners and bad backend answers without issuing a cookie", async () => {
  const backend = fakeBackend(() => jsonResponse({ url: "https://evil.example/phish" }));
  const member = await startProviderConnect({ provider: "slack", body: {}, origin: ORIGIN, session: { slug: "acme-studio", role: "member" }, backend: backend.fetch });
  assert.equal(member.status, 403);
  assert.equal(backend.calls.length, 0);
  const bad = await startProviderConnect({ provider: "slack", body: {}, origin: ORIGIN, session: owner, backend: backend.fetch });
  assert.equal(bad.status, 502);
  assert.equal(bad.cookie, undefined);
  const down = await startProviderConnect({ provider: "slack", body: {}, origin: ORIGIN, session: owner, backend: async () => { throw new Error("connect ECONNREFUSED secret-host"); } });
  assert.equal(down.status, 503);
  assert.equal(JSON.stringify(down.body).includes("secret-host"), false);
  assert.equal(down.cookie, undefined);
  const refused = await startProviderConnect({ provider: "bunq" as never, body: {}, origin: ORIGIN, session: owner, backend: backend.fetch });
  assert.equal(refused.status, 400);
});

const callbackUrl = (qs: string) => new URL(`${ORIGIN}/api/onboarding/callback/google?${qs}`);
const cookie = encodeProviderCookie("acme-studio", "proof-1");

test("callback forwards code, state, proof and derived redirect, then reports success only when the step is connected", async () => {
  const backend = fakeBackend(() => jsonResponse(setupWith("email", "connected")));
  const result = await completeProviderCallback({ provider: "google", origin: ORIGIN, url: callbackUrl("state=st1&code=cd1"), cookieValue: cookie, session: owner, backend: backend.fetch });
  assert.equal(result.location, "https://scout.example/acme-studio?setup=google-connected#setup");
  assert.equal(backend.calls[0].path, "/v1/onboarding/callback/google");
  assert.deepEqual(JSON.parse(String(backend.calls[0].init.body)), { state: "st1", code: "cd1", redirect_uri: "https://scout.example/api/onboarding/callback/google", browser_proof: "proof-1" });
});

test("callback treats a not-connected step, backend failure, outage and cancelled consent as failed", async () => {
  const notConnected = fakeBackend(() => jsonResponse(setupWith("email", "failed")));
  assert.equal((await completeProviderCallback({ provider: "google", origin: ORIGIN, url: callbackUrl("state=s&code=c"), cookieValue: cookie, session: owner, backend: notConnected.fetch })).location, "https://scout.example/acme-studio?setup=google-failed#setup");
  const rejected = fakeBackend(() => jsonResponse({ detail: "SECRET" }, 400));
  assert.equal((await completeProviderCallback({ provider: "google", origin: ORIGIN, url: callbackUrl("state=s&code=c"), cookieValue: cookie, session: owner, backend: rejected.fetch })).location, "https://scout.example/acme-studio?setup=google-failed#setup");
  const down = await completeProviderCallback({ provider: "google", origin: ORIGIN, url: callbackUrl("state=s&code=c"), cookieValue: cookie, session: owner, backend: async () => { throw new Error("boom SECRET"); } });
  assert.equal(down.location, "https://scout.example/acme-studio?setup=google-failed#setup");
  assert.equal(down.location.includes("SECRET"), false);
  // Provider error/cancel is still forwarded so the single-use state is consumed, and is never success.
  const ok = fakeBackend(() => jsonResponse(setupWith("email", "connected")));
  const cancelled = await completeProviderCallback({ provider: "google", origin: ORIGIN, url: callbackUrl("state=s&error=access_denied"), cookieValue: cookie, session: owner, backend: ok.fetch });
  assert.equal(cancelled.location, "https://scout.example/acme-studio?setup=google-failed#setup");
  assert.deepEqual(JSON.parse(String(ok.calls[0].init.body)), { state: "s", error: "access_denied", redirect_uri: "https://scout.example/api/onboarding/callback/google", browser_proof: "proof-1" });
  // The Slack step is checked for Slack, not the email step.
  const slack = fakeBackend(() => jsonResponse(setupWith("email", "connected")));
  assert.equal((await completeProviderCallback({ provider: "slack", origin: ORIGIN, url: callbackUrl("state=s&code=c"), cookieValue: cookie, session: owner, backend: slack.fetch })).location, "https://scout.example/acme-studio?setup=slack-failed#setup");
});

test("callback refuses missing cookie, other sessions, non-owners and malformed queries before calling the backend", async () => {
  const backend = fakeBackend(() => jsonResponse(setupWith("email", "connected")));
  const failed = "https://scout.example/acme-studio?setup=google-failed#setup";
  const run = (over: Partial<Parameters<typeof completeProviderCallback>[0]>) => completeProviderCallback({ provider: "google", origin: ORIGIN, url: callbackUrl("state=s&code=c"), cookieValue: cookie, session: owner, backend: backend.fetch, ...over });
  assert.equal((await run({ session: null })).location, failed);
  assert.equal((await run({ session: { slug: "acme-studio", role: "member" } })).location, failed);
  assert.equal((await run({ session: { slug: "other-org", role: "owner" } })).location, failed);
  assert.equal((await run({ url: callbackUrl("state=a&state=b&code=c") })).location, failed);
  assert.equal((await run({ url: callbackUrl("code=c") })).location, failed);
  assert.equal((await run({ url: callbackUrl(`state=s&code=${"c".repeat(4097)}`) })).location, failed);
  assert.equal((await run({ url: callbackUrl("state=s&code=a&code=b") })).location, failed);
  assert.equal(backend.calls.length, 0);
  // No cookie means no trustworthy slug: go to the neutral home, never a guessed organization.
  assert.equal((await run({ cookieValue: undefined, session: null })).location, "https://scout.example/");
  assert.equal((await run({ cookieValue: "garbage" })).location, "https://scout.example/");
  assert.equal(backend.calls.length, 0);
});

test("registration lands on setup, ordinary sign-in does not", () => {
  assert.equal(registrationRedirect("acme-studio"), "/acme-studio#setup");
  assert.equal(googleSignInRedirect(ORIGIN, "acme-studio", { registered: true, linked: false }), "https://scout.example/acme-studio#setup");
  assert.equal(googleSignInRedirect(ORIGIN, "acme-studio", { registered: false, linked: false }), "https://scout.example/acme-studio");
  assert.equal(googleSignInRedirect(ORIGIN, "acme-studio", { registered: false, linked: true }), "https://scout.example/acme-studio?auth=linked");
});
