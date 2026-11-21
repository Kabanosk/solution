import { test } from "node:test";
import assert from "node:assert/strict";
import { buildHandoff } from "../lib/onboarding-handoff.ts";
import type { Setup } from "../lib/onboarding-model.ts";

const ORIGIN = "https://scout.example";
const SECRETS = ["ya29.SECRET", "1//REFRESH", "xoxb-SECRET", "ops@acme.example", "tenant_0123456789abcdef", "WireGuard-PRIVATE", "Ola IT", "sb_api_KEY"];
const setup: Setup = {
  version: 1, status: "in_progress", revision: 5,
  steps: [
    { id: "bank", state: "sandbox_connected", provider: "bunq", message: "sb_api_KEY", display: { account: "tenant_0123456789abcdef" } },
    { id: "invoicing", state: "connected", provider: "builtin", message: null, display: {} },
    { id: "ksef", state: "needs_configuration", provider: "ksef", message: "Missing token", display: {} },
    { id: "email", state: "connected", provider: "google", message: null, display: { mailbox: "ops@acme.example", token: "ya29.SECRET", refresh: "1//REFRESH" } },
    { id: "slack", state: "failed", provider: "slack", message: "xoxb-SECRET", display: {} },
    { id: "network", state: "needs_configuration", provider: null, message: null, display: {} },
  ],
  network: { required: true, provider: "WireGuard-PRIVATE", protocol: "udp", systems: ["ERP"], administrator: "Ola IT", company_only: true },
};
const everything = (h: ReturnType<typeof buildHandoff>) => JSON.stringify(h) + h.markdown + h.prompt + (h.claudeUrl ?? "") + decodeURIComponent(h.claudeUrl ?? "");

test("handoff exposes only allowlisted, secret-free setup state", () => {
  const handoff = buildHandoff({ origin: ORIGIN, slug: "acme-studio", setup });
  const text = everything(handoff);
  for (const secret of SECRETS) assert.equal(text.includes(secret), false, secret);
  assert.equal(handoff.setupUrl, "https://scout.example/acme-studio#setup");
  assert.equal(handoff.manifest.schema, "scout.setup/v1");
  const manifest = handoff.manifest as { workspace: { slug: string }; app_origin: string; steps: { id: string; state: string; status_text: string; next_action: string }[] };
  assert.equal(manifest.app_origin, ORIGIN);
  assert.equal(manifest.workspace.slug, "acme-studio");
  assert.deepEqual(manifest.steps.map(s => [s.id, s.state]), [["bank", "sandbox_connected"], ["invoicing", "connected"], ["ksef", "needs_configuration"], ["email", "connected"], ["slack", "failed"], ["network", "needs_configuration"]]);
  assert.deepEqual(Object.keys(handoff).sort(), ["claudeUrl", "manifest", "markdown", "prompt", "setupUrl"]);
});

test("handoff states honestly what is not configured and what is only a sandbox", () => {
  const { markdown } = buildHandoff({ origin: ORIGIN, slug: "acme-studio", setup });
  assert.match(markdown, /sandbox/i);
  assert.match(markdown, /not (yet )?(configured|connected)|needs configuration|failed/i);
  assert.doesNotMatch(markdown, /everything is connected|all connected/i);
  const empty = buildHandoff({ origin: ORIGIN, slug: "acme-studio", setup: null });
  assert.equal((empty.manifest as { steps: { state: string }[] }).steps.every(s => s.state === "not_started"), true);
  assert.equal((empty.manifest as { steps: unknown[] }).steps.length, 6);
});

test("handoff tells any agent to use the person's own sign-in and not to change Scout", () => {
  const { markdown, prompt } = buildHandoff({ origin: ORIGIN, slug: "acme-studio", setup });
  for (const text of [markdown, prompt]) {
    assert.match(text, /do not modify/i);
    assert.match(text, /source|code/i);
    assert.match(text, /sign in/i);
    assert.match(text, /consent|authoriz/i);
    assert.match(text, /parallel/i);
  }
  assert.match(markdown, /password|API key|secret/i);
  assert.doesNotMatch(markdown + prompt, /Bearer|Authorization:|cookie value/i);
  assert.doesNotMatch(markdown + prompt, /codex:\/\//i);
});

test("handoff preserves only the boolean upstream revocation warning, never provider diagnostics", () => {
  const warned = { ...setup, steps: setup.steps.map(s => s.id === "email" ? { ...s, state: "not_started" as const, message: "ya29.SECRET", display: { revocation_unconfirmed: true, token: "ya29.SECRET" } } : s) };
  const handoff = buildHandoff({ origin: ORIGIN, slug: "acme-studio", setup: warned });
  const email = (handoff.manifest.steps as { id: string; revocation_unconfirmed?: boolean; next_action: string }[]).find(s => s.id === "email")!;
  assert.equal(email.revocation_unconfirmed, true);
  for (const text of [email.next_action, handoff.markdown, handoff.prompt]) assert.match(text, /revocation.*unconfirmed/i);
  assert.equal(everything(handoff).includes("ya29.SECRET"), false);
});

test("claude shortcut is q-only, bounded below 5000 characters and optional", () => {
  const handoff = buildHandoff({ origin: ORIGIN, slug: "acme-studio", setup });
  assert.ok(handoff.claudeUrl);
  const url = new URL(handoff.claudeUrl!);
  assert.equal(url.protocol, "claude-cli:");
  assert.deepEqual([...url.searchParams.keys()], ["q"]);
  assert.equal(url.searchParams.get("q"), handoff.prompt);
  assert.ok(handoff.claudeUrl!.length < 5000);
  // A pathological slug/origin cannot push the link over the verified limit; it is dropped instead of truncated.
  const long = buildHandoff({ origin: "https://" + "a".repeat(6000) + ".example", slug: "acme-studio", setup });
  assert.equal(long.claudeUrl, null);
});

test("handoff refuses insecure or unvalid origins and slugs", () => {
  assert.throws(() => buildHandoff({ origin: "http://scout.example", slug: "acme-studio", setup: null }), /https/i);
  assert.doesNotThrow(() => buildHandoff({ origin: "http://127.0.0.1:3000", slug: "acme-studio", setup: null }));
  assert.throws(() => buildHandoff({ origin: ORIGIN, slug: "../x", setup: null }), /slug/i);
  assert.throws(() => buildHandoff({ origin: ORIGIN + "/path", slug: "acme-studio", setup: null }), /origin/i);
});

test("handoff ignores unknown step ids and states from a hostile or newer backend", () => {
  const hostile = { ...setup, steps: [{ id: "vpn-secret", state: "connected", provider: null, message: null, display: {} }, { id: "bank", state: "ya29.SECRET", provider: null, message: null, display: {} }] } as unknown as Setup;
  const handoff = buildHandoff({ origin: ORIGIN, slug: "acme-studio", setup: hostile });
  assert.equal(everything(handoff).includes("ya29.SECRET"), false);
  assert.equal(everything(handoff).includes("vpn-secret"), false);
  assert.equal((handoff.manifest as { steps: { id: string; state: string }[] }).steps.find(s => s.id === "bank")?.state, "not_started");
});
