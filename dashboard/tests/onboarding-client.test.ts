import { test } from "node:test";
import assert from "node:assert/strict";
import {
  STEPS, buildNetworkAnswers, canComplete, canEdit, completionAction, connectProvider, createHandoff, disconnectProvider, fetchSetup,
  handoffFiles, OnboardingError, parseSetup, patchSetup, parseSlackChannelIds, saveSlackChannels, safeAuthorizationUrl, shouldOpenSetup, startOAuth, stepStatus, validNip,
  type Fetcher,
} from "../lib/onboarding-client.ts";
import { routeFromHash, NAV_ITEMS } from "../lib/nav.ts";
import type { Setup, SetupStep, SetupStepId, SetupStepState } from "../lib/onboarding-model.ts";

const IDS: SetupStepId[] = ["bank", "invoicing", "ksef", "email", "slack", "network"];
const step = (id: SetupStepId, state: SetupStepState = "not_started", extra: Partial<SetupStep> = {}): SetupStep => ({ id, state, provider: null, message: null, display: {}, ...extra });
const setup = (over: Partial<Setup> = {}, states: Partial<Record<SetupStepId, SetupStepState>> = {}): Setup =>
  ({ version: 1, status: "pending", revision: 3, steps: IDS.map(id => step(id, states[id])), network: null, ...over });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const recorder = (...responses: Response[]) => {
  const calls: { path: string; init: RequestInit }[] = [];
  const fetcher: Fetcher = async (path, init = {}) => { calls.push({ path, init }); const next = responses.shift(); if (!next) throw new Error("unexpected request"); return next; };
  return { calls, fetcher };
};

test("Slack channel choices are bounded IDs and use the authenticated JSON write path", async () => {
  assert.deepEqual(parseSlackChannelIds("C12345678, G12345678\nC12345678"), ["C12345678", "G12345678"]);
  assert.deepEqual(parseSlackChannelIds(""), []);
  assert.equal(parseSlackChannelIds("#general"), null);
  assert.equal(parseSlackChannelIds("https://other.example/channel"), null);
  assert.equal(parseSlackChannelIds("C123"), null);
  const { calls, fetcher } = recorder(json(setup({}, { slack: "connected" })));
  await saveSlackChannels(fetcher, ["C12345678"]);
  assert.equal(calls[0].path, "/api/onboarding/slack/channels");
  assert.equal(calls[0].init.method, "PUT");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { channels: ["C12345678"] });
  assert.equal(new Headers(calls[0].init.headers).get("content-type"), "application/json");
});

test("unconfirmed provider revocation remains visible rather than looking successfully removed upstream", () => {
  assert.equal(stepStatus(step("email", "not_started", { display: { revocation_unconfirmed: true } })).tone, "warning");
});

test("email guidance distinguishes Scout label enforcement and Google's shared grant revocation", () => {
  const guidance = STEPS.find(step => step.id === "email")!.description;
  assert.match(guidance, /Google grants read-only mailbox permission/i);
  assert.match(guidance, /Scout restricts reads to one label/i);
  assert.match(guidance, /revocation.*other.*workspace/i);
});

test("catalog keeps the six contract steps in order with the exact calls to action", () => {
  assert.deepEqual(STEPS.map(entry => entry.id), IDS);
  assert.deepEqual(STEPS.map(entry => entry.cta), ["Connect sandbox bank", "Use built-in invoicing", "Set up test environment", "Continue with Google", "Connect workspace", "Answer a few questions"]);
});

test("parseSetup accepts the contract shape and orders steps", () => {
  const raw = setup(); raw.steps.reverse();
  assert.deepEqual(parseSetup(raw).steps.map(entry => entry.id), IDS);
});

test("parseSetup rejects malformed or unknown responses instead of guessing", () => {
  assert.throws(() => parseSetup(null), OnboardingError);
  assert.throws(() => parseSetup({ ...setup(), version: 2 }), OnboardingError);
  assert.throws(() => parseSetup({ ...setup(), status: "weird" }), OnboardingError);
  assert.throws(() => parseSetup({ ...setup(), steps: setup().steps.slice(1) }), OnboardingError);
  assert.throws(() => parseSetup({ ...setup(), steps: [...setup().steps.slice(1), { ...step("bank"), state: "nope" }] }), OnboardingError);
});

test("step status labels never present sandbox, prerequisites or skipped as an actual connection", () => {
  assert.equal(stepStatus(step("bank", "connected")).label, "Connected");
  assert.equal(stepStatus(step("bank", "sandbox_connected")).label, "Sandbox connected");
  assert.equal(stepStatus(step("ksef", "needs_configuration")).label, "Needs setup");
  assert.equal(stepStatus(step("network", "needs_configuration")).label, "Administrator setup required");
  assert.equal(stepStatus(step("slack", "failed")).tone, "danger");
  assert.equal(stepStatus(step("email", "skipped")).label, "Skipped");
  assert.equal(stepStatus(step("email", "connecting")).label, "Connecting");
  assert.equal(stepStatus(step("email", "not_started")).label, "Not started");
});

test("setup opens only for an owner without an explicit hash and a pending or in-progress setup", () => {
  assert.equal(shouldOpenSetup({ hash: "", role: "owner", status: "pending" }), true);
  assert.equal(shouldOpenSetup({ hash: "", role: "owner", status: "in_progress" }), true);
  assert.equal(shouldOpenSetup({ hash: "", role: "owner", status: "not_started" }), false, "legacy organizations stay on chat");
  assert.equal(shouldOpenSetup({ hash: "", role: "owner", status: "dismissed" }), false);
  assert.equal(shouldOpenSetup({ hash: "", role: "owner", status: "completed" }), false);
  assert.equal(shouldOpenSetup({ hash: "#tasks", role: "owner", status: "pending" }), false, "deep links are preserved");
  assert.equal(shouldOpenSetup({ hash: "#chat", role: "owner", status: "pending" }), false);
  assert.equal(shouldOpenSetup({ hash: "", role: "member", status: "pending" }), false);
});

test("only owners edit", () => { assert.equal(canEdit("owner"), true); assert.equal(canEdit("member"), false); assert.equal(canEdit(undefined), false); });

test("completion needs every step configured or skipped, otherwise continuing dismisses", () => {
  const done = setup({}, { bank: "sandbox_connected", invoicing: "connected", ksef: "skipped", email: "connected", slack: "skipped", network: "skipped" });
  assert.equal(canComplete(done), true);
  assert.equal(completionAction(done), "complete");
  const open = setup({}, { bank: "failed", invoicing: "connected", ksef: "skipped", email: "connected", slack: "skipped", network: "skipped" });
  assert.equal(canComplete(open), false);
  assert.equal(completionAction(open), "skip");
  assert.equal(canComplete(setup()), false);
});

test("NIP validation accepts ten digits only", () => {
  assert.equal(validNip("5252248481"), true);
  assert.equal(validNip("525-224-84-81"), false);
  assert.equal(validNip("123"), false);
  assert.equal(validNip("12345678901"), false);
});

test("network answers: not required needs nothing; required needs provider, protocol, administrator, systems and company-only consent", () => {
  const none = buildNetworkAnswers({ required: false, provider: "", protocol: "", administrator: "", systems: "", companyOnly: false });
  assert.deepEqual(none, { ok: true, answers: { required: false, provider: "", protocol: "", administrator: "", systems: [], company_only: false } });
  const missing = buildNetworkAnswers({ required: true, provider: "", protocol: "", administrator: "", systems: "", companyOnly: false });
  assert.equal(missing.ok, false);
  if (!missing.ok) assert.deepEqual(Object.keys(missing.errors).sort(), ["administrator", "companyOnly", "protocol", "provider", "systems"]);
  const good = buildNetworkAnswers({ required: true, provider: " WireGuard ", protocol: "UDP", administrator: "it@example.test", systems: "ERP, file server\nHR share, ERP", companyOnly: true });
  assert.deepEqual(good, { ok: true, answers: { required: true, provider: "WireGuard", protocol: "UDP", administrator: "it@example.test", systems: ["ERP", "file server", "HR share"], company_only: true } });
});

test("network answers refuse secrets, key material and oversized values", () => {
  const base = { required: true, provider: "WireGuard", protocol: "UDP", administrator: "IT", systems: "ERP", companyOnly: true };
  for (const [field, value] of [["provider", "-----BEGIN PRIVATE KEY-----"], ["administrator", "password: hunter2"], ["systems", "x".repeat(200)], ["protocol", "PrivateKey = abc"]] as const) {
    const result = buildNetworkAnswers({ ...base, [field]: value });
    assert.equal(result.ok, false, field);
    if (!result.ok) assert.ok(result.errors[field], field);
  }
  const many = buildNetworkAnswers({ ...base, systems: Array.from({ length: 21 }, (_, index) => `system ${index}`).join(",") });
  assert.equal(many.ok, false);
});

test("fetchSetup reads GET /api/onboarding", async () => {
  const { calls, fetcher } = recorder(json(setup()));
  assert.equal((await fetchSetup(fetcher)).revision, 3);
  assert.equal(calls[0].path, "/api/onboarding");
  assert.equal(calls[0].init.method ?? "GET", "GET");
});

test("server failures surface as errors and 409 is flagged as a conflict", async () => {
  await assert.rejects(fetchSetup(recorder(json({ error: "Down" }, 503)).fetcher), (error: unknown) => error instanceof OnboardingError && error.status === 503 && error.message === "Down");
  await assert.rejects(patchSetup(recorder(json({}, 409)).fetcher, { revision: 3, action: "skip" }), (error: unknown) => error instanceof OnboardingError && error.conflict);
  await assert.rejects(fetchSetup(recorder(json({}, 403)).fetcher), /owner/i);
  await assert.rejects(fetchSetup(async () => { throw new TypeError("offline"); }), OnboardingError);
});

test("an already-connected account is not mistaken for a stale setup revision", async () => {
  await assert.rejects(startOAuth(recorder(json({ error: "Disconnect the existing provider connection before connecting again.", code: "already_connected" }, 409)).fetcher, "google"),
    (error: unknown) => error instanceof OnboardingError && !error.conflict && /Disconnect/.test(error.message));
});

test("patchSetup sends the revision with the action as JSON", async () => {
  const { calls, fetcher } = recorder(json(setup({ status: "dismissed", revision: 4 })));
  const next = await patchSetup(fetcher, { revision: 3, action: "skip" });
  assert.equal(next.status, "dismissed");
  assert.equal(calls[0].path, "/api/onboarding");
  assert.equal(calls[0].init.method, "PATCH");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { revision: 3, action: "skip" });
  assert.equal(new Headers(calls[0].init.headers).get("content-type"), "application/json");
});

test("connectProvider posts to the provider path and never reports success on a failure", async () => {
  const ok = recorder(json(setup({}, { bank: "sandbox_connected" })));
  const next = await connectProvider(ok.fetcher, "bunq", {});
  assert.equal(next.steps[0].state, "sandbox_connected");
  assert.equal(ok.calls[0].path, "/api/onboarding/connect/bunq");
  assert.equal(ok.calls[0].init.method, "POST");
  await assert.rejects(connectProvider(recorder(json({ error: "bunq is unreachable" }, 502)).fetcher, "bunq", {}), /bunq is unreachable/);
});

test("disconnectProvider satisfies the real write-route JSON contract", async () => {
  const { calls, fetcher } = recorder(json(setup()));
  await disconnectProvider(fetcher, "slack");
  assert.equal(calls[0].path, "/api/onboarding/connect/slack");
  assert.equal(calls[0].init.method, "DELETE");
  assert.equal(new Headers(calls[0].init.headers).get("content-type"), "application/json");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), {});
});

test("startOAuth sends no redirect or proof (the server owns them) and returns the provider URL", async () => {
  const { calls, fetcher } = recorder(json({ url: "https://accounts.google.com/o/oauth2/v2/auth?state=x" }), json({ url: "https://slack.com/oauth/v2/authorize?state=y" }));
  assert.deepEqual(await startOAuth(fetcher, "google", { label: "INBOX" }), { url: "https://accounts.google.com/o/oauth2/v2/auth?state=x" });
  assert.equal(calls[0].path, "/api/onboarding/connect/google");
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), { label: "INBOX" });
  await startOAuth(fetcher, "slack");
  assert.equal(calls[1].path, "/api/onboarding/connect/slack");
  assert.deepEqual(JSON.parse(String(calls[1].init.body)), {});
});

test("startOAuth surfaces a missing-credentials prerequisite (200 Setup, no URL) instead of a fake redirect", async () => {
  const blocked = setup({}, { email: "needs_configuration" });
  blocked.steps[3].message = "Google mailbox credentials are not configured on this server.";
  const result = await startOAuth(recorder(json(blocked)).fetcher, "google");
  assert.ok("setup" in result);
  if ("setup" in result) assert.equal(result.setup.steps[3].message, "Google mailbox credentials are not configured on this server.");
});

test("startOAuth rejects a response without a safe authorization URL", async () => {
  await assert.rejects(startOAuth(recorder(json({ url: "javascript:alert(1)" })).fetcher, "slack"), OnboardingError);
  await assert.rejects(startOAuth(recorder(json({})).fetcher, "slack"), OnboardingError);
});

test("safeAuthorizationUrl allows https and loopback http only", () => {
  assert.equal(safeAuthorizationUrl("https://slack.com/oauth/v2/authorize?x=1"), "https://slack.com/oauth/v2/authorize?x=1");
  assert.equal(safeAuthorizationUrl("http://127.0.0.1:9000/mock"), "http://127.0.0.1:9000/mock");
  assert.equal(safeAuthorizationUrl("http://evil.example/"), null);
  assert.equal(safeAuthorizationUrl("data:text/html,x"), null);
  assert.equal(safeAuthorizationUrl("not a url"), null);
});

test("handoff request and artifacts: Markdown and JSON files, Claude shortcut only for the claude-cli scheme", async () => {
  const body = { setupUrl: "https://scout.example/acme#setup", markdown: "# Setup", manifest: { version: "scout.setup/v1" }, prompt: "Help me", claudeUrl: "claude-cli://open?q=hi" };
  const { calls, fetcher } = recorder(json(body));
  const handoff = await createHandoff(fetcher);
  assert.equal(calls[0].path, "/api/onboarding/handoff");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(handoff.claudeUrl, "claude-cli://open?q=hi");
  const files = handoffFiles(handoff);
  assert.deepEqual(files.map(file => file.name), ["scout-setup.md", "scout-setup.json"]);
  assert.equal(files[0].content, "# Setup");
  assert.deepEqual(JSON.parse(files[1].content), { version: "scout.setup/v1" });
  const hostile = await createHandoff(recorder(json({ ...body, claudeUrl: "javascript:alert(1)" })).fetcher);
  assert.equal(hostile.claudeUrl, null);
  await assert.rejects(createHandoff(recorder(json({ setupUrl: 1 })).fetcher), OnboardingError);
});

test("#setup is a native route and does not become a seventh navigation item", () => {
  assert.deepEqual(routeFromHash("#setup"), { view: "setup" });
  assert.equal(NAV_ITEMS.some(entry => entry.route.view === "setup"), false);
});
