// Server-side helpers for /api/onboarding/**. Deliberately free of next/* and server-only imports so the
// security-relevant logic (validation, OAuth proof binding, callback outcome) is unit-testable.
import { newToken, validSlug } from "./auth-crypto.ts";
import type { NetworkAnswers, Setup, SetupStatus, SetupStep, SetupStepId, SetupStepState, SetupUpdate } from "./onboarding-model.ts";

export const PROVIDERS = ["bunq", "builtin", "ksef", "google", "slack"] as const;
export type Provider = (typeof PROVIDERS)[number];
export type OAuthProvider = "google" | "slack";
export const STEP_IDS: readonly SetupStepId[] = ["bank", "invoicing", "ksef", "email", "slack", "network"];
export const STEP_STATES: readonly SetupStepState[] = ["not_started", "needs_configuration", "connecting", "sandbox_connected", "connected", "failed", "skipped"];
const STATUSES: readonly SetupStatus[] = ["not_started", "pending", "in_progress", "dismissed", "completed"];
const PROOF_TTL_SECONDS = 600;
const OAUTH_STEP: Record<OAuthProvider, SetupStepId> = { google: "email", slack: "slack" };
const isOAuth = (provider: Provider): provider is OAuthProvider => provider === "google" || provider === "slack";

type Parsed<T> = { ok: true; value: T } | { ok: false; error: string };
const bad = (error = "Invalid setup request."): { ok: false; error: string } => ({ ok: false, error });
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown, max: number) => typeof value === "string" && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);

export function parseProvider(value: unknown): Provider | null {
  return typeof value === "string" && (PROVIDERS as readonly string[]).includes(value) ? value as Provider : null;
}

export function parseConnectBody(provider: Provider, body: Record<string, unknown>): Parsed<Record<string, string>> {
  if (provider === "builtin") return { ok: true, value: {} };
  if (provider === "bunq") {
    if (body.api_key === undefined) return { ok: true, value: {} };
    if (typeof body.api_key !== "string" || body.api_key.length > 1024) return bad();
    const key = body.api_key.trim();
    if (key.length > 256 || /[\u0000-\u001f\u007f\s]/.test(key)) return bad();
    return { ok: true, value: key ? { api_key: key } : {} };
  }
  if (provider === "ksef") return typeof body.nip === "string" && /^\d{10}$/.test(body.nip) ? { ok: true, value: { nip: body.nip } } : bad("Enter a 10-digit NIP.");
  // Google/Slack: redirect_uri and browser_proof are server-derived; whatever the browser sent is ignored.
  if (body.label === undefined) return { ok: true, value: {} };
  return typeof body.label === "string" && body.label.length >= 1 && text(body.label, 100) ? { ok: true, value: { label: body.label } } : bad();
}

export function parseSlackChannels(body: Record<string, unknown>): Parsed<{ channels: string[] }> {
  if (!Array.isArray(body.channels) || body.channels.length > 100 || !body.channels.every(id => typeof id === "string" && /^[CG][A-Z0-9]{8,14}$/.test(id))) return bad("Use at most 100 Slack channel IDs.");
  return { ok: true, value: { channels: [...new Set(body.channels as string[])] } };
}

function parseNetwork(value: unknown): NetworkAnswers | null {
  if (!isRecord(value) || typeof value.required !== "boolean" || typeof value.company_only !== "boolean") return null;
  if (!text(value.provider, 100) || !text(value.protocol, 100) || !text(value.administrator, 200)) return null;
  if (!Array.isArray(value.systems) || value.systems.length > 20 || !value.systems.every(s => typeof s === "string" && s.length <= 100 && !/[\u0000-\u001f\u007f]/.test(s))) return null;
  return { required: value.required, provider: value.provider as string, protocol: value.protocol as string, systems: [...value.systems as string[]], administrator: value.administrator as string, company_only: value.company_only };
}

export function parseSetupUpdate(body: Record<string, unknown>): Parsed<SetupUpdate> {
  const { revision, action } = body;
  if (typeof revision !== "number" || !Number.isSafeInteger(revision) || revision < 0) return bad();
  if (action === "skip" || action === "resume" || action === "complete") return body.step === undefined && body.state === undefined && body.network === undefined ? { ok: true, value: { revision, action } } : bad();
  if (action === "step") {
    const step = STEP_IDS.find(id => id === body.step);
    if (!step || (body.state !== "skipped" && body.state !== "not_started")) return bad();
    return { ok: true, value: { revision, action, step, state: body.state } };
  }
  if (action === "network") {
    const network = parseNetwork(body.network);
    return network ? { ok: true, value: { revision, action, network } } : bad();
  }
  return bad();
}

export const redirectUri = (origin: string, provider: OAuthProvider) => `${origin}/api/onboarding/callback/${provider}`;
export const providerCookieName = (provider: OAuthProvider) => `scout_onboarding_${provider}`;
export const providerCookiePath = (provider: OAuthProvider) => `/api/onboarding/callback/${provider}`;
export const encodeProviderCookie = (slug: string, proof: string) => `${slug}.${proof}`;
export function parseProviderCookie(value: string | undefined): { slug: string; proof: string } | null {
  if (!value) return null;
  const parts = value.split(".");
  if (parts.length !== 2) return null;
  const [slug, proof] = parts;
  return validSlug(slug) && /^[A-Za-z0-9_-]{1,128}$/.test(proof) ? { slug, proof } : null;
}

export function validProviderAuthUrl(provider: OAuthProvider, value: unknown): boolean {
  if (typeof value !== "string" || value.length > 4096) return false;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return false;
    return provider === "google" ? url.hostname === "accounts.google.com" : url.hostname === "slack.com" || url.hostname.endsWith(".slack.com");
  } catch { return false; }
}

const shortText = (value: unknown, max: number) => typeof value === "string" ? value.slice(0, max) : null;
const DISPLAY_FIELDS: Record<SetupStepId, readonly string[]> = {
  bank: ["sandbox", "environment", "accounts", "read_only", "account_name", "currency", "account", "iban"],
  invoicing: ["name", "provider", "system", "documents"],
  ksef: ["nip", "environment", "company", "read_only"],
  email: ["mailbox", "label", "scope", "read_only", "connected"],
  slack: ["authorized", "channels", "channel_ids", "team", "team_id", "team_name", "workspace", "workspace_name", "connected"],
  network: ["requires_admin", "required", "provider", "protocol", "systems", "administrator", "company_only"],
};
function sanitizeStep(raw: unknown): SetupStep | null {
  if (!isRecord(raw)) return null;
  const id = STEP_IDS.find(candidate => candidate === raw.id), state = STEP_STATES.find(candidate => candidate === raw.state);
  if (!id || !state) return null;
  const display: SetupStep["display"] = {};
  if (isRecord(raw.display)) for (const [key, value] of Object.entries(raw.display).slice(0, 20)) {
    if (key === "revocation_unconfirmed") { if (typeof value === "boolean") display[key] = value; continue; }
    if (!DISPLAY_FIELDS[id].includes(key)) continue;
    if (typeof value === "string") display[key] = value.slice(0, key === "channel_ids" ? 1600 : 200);
    else if (typeof value === "boolean" || value === null) display[key] = value;
  }
  return { id, state, provider: shortText(raw.provider, 40), message: shortText(raw.message, 300), display };
}

/** Rebuilds the wire contract so an unexpected backend field (token, schema name, ...) can never reach the browser. */
export function sanitizeSetup(raw: unknown): Setup | null {
  if (!isRecord(raw) || raw.version !== 1 || typeof raw.revision !== "number" || !Number.isSafeInteger(raw.revision) || !Array.isArray(raw.steps)) return null;
  const status = STATUSES.find(candidate => candidate === raw.status);
  if (!status) return null;
  const steps = raw.steps.map(sanitizeStep).filter((step): step is SetupStep => step !== null);
  return { version: 1, status, revision: raw.revision, steps, network: parseNetwork(raw.network) };
}

const GENERIC: Record<number, [number, string]> = {
  400: [400, "That setup request was not accepted."], 422: [400, "That setup request was not accepted."],
  401: [401, "Access denied."], 403: [403, "Only a workspace owner can change setup."], 404: [404, "Setup option not found."],
  409: [409, "Setup changed elsewhere. Reload and try again."], 429: [429, "Too many requests. Try again later."],
};
/** Backend bodies are never forwarded on failure: only a generic message keyed by status. */
export function mapBackendResult(status: number, data: unknown): { status: number; body: unknown } {
  if (status >= 200 && status < 300) {
    const setup = sanitizeSetup(data);
    return setup ? { status: 200, body: setup } : { status: 502, body: { error: "Scout setup returned an unexpected response." } };
  }
  if (status === 409 && isRecord(data) && isRecord(data.error) && data.error.code === "already_connected") {
    return { status: 409, body: { error: "Disconnect the existing provider connection before connecting again.", code: "already_connected" } };
  }
  const [mapped, error] = GENERIC[status] ?? [502, "Scout setup is temporarily unavailable."];
  return { status: mapped, body: { error } };
}

export type BackendFetch = (path: string, init?: RequestInit) => Promise<Response>;
/** Explicit-session backend caller for routes (OAuth callback) where the proxy-provided organization header is absent. */
export function makeBackendFetch(options: { apiBase: string; token: string; slug: string; timeoutMs?: number; fetchImpl?: typeof fetch }): BackendFetch {
  const doFetch = options.fetchImpl ?? fetch;
  return (path, init = {}) => {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${options.token}`);
    headers.set("X-Scout-Organization", options.slug);
    if (init.body !== undefined) headers.set("Content-Type", "application/json");
    return doFetch(`${options.apiBase}${path}`, { ...init, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(Math.min(options.timeoutMs ?? 10000, 10000)), headers });
  };
}

type SessionView = { slug: string; role: string };
export type ProviderCookie = { name: string; value: string; path: string; maxAge: number };

export async function startProviderConnect(input: { provider: OAuthProvider; body: Record<string, unknown>; origin: string; session: SessionView; backend: BackendFetch; newProof?: () => string }):
  Promise<{ status: number; body: unknown; cookie?: ProviderCookie }> {
  const { provider, session } = input;
  if (!isOAuth(provider)) return { status: 400, body: { error: "That setup request was not accepted." } };
  if (session.role !== "owner") return { status: 403, body: { error: GENERIC[403][1] } };
  const parsed = parseConnectBody(provider, input.body);
  if (!parsed.ok) return { status: 400, body: { error: parsed.error } };
  const proof = (input.newProof ?? newToken)();
  let response: Response;
  try {
    response = await input.backend(`/v1/onboarding/connect/${provider}`, { method: "POST", body: JSON.stringify({ redirect_uri: redirectUri(input.origin, provider), browser_proof: proof, ...parsed.value }) });
  } catch { return { status: 503, body: { error: "Scout setup is temporarily unavailable." } }; }
  const data = await response.json().catch(() => null) as { url?: unknown } | null;
  if (!response.ok) return mapBackendResult(response.status, data);
  if (sanitizeSetup(data)) return mapBackendResult(response.status, data);
  if (!validProviderAuthUrl(provider, data?.url)) return { status: 502, body: { error: "Scout setup returned an unexpected response." } };
  return { status: 200, body: { url: data!.url }, cookie: { name: providerCookieName(provider), value: encodeProviderCookie(session.slug, proof), path: providerCookiePath(provider), maxAge: PROOF_TTL_SECONDS } };
}

/** Result is only a redirect location; the caller always clears the provider cookie. */
export async function completeProviderCallback(input: { provider: OAuthProvider; origin: string; url: URL; cookieValue: string | undefined; session: SessionView | null; backend: BackendFetch }): Promise<{ location: string }> {
  const { provider, origin } = input;
  const proof = parseProviderCookie(input.cookieValue);
  if (!proof) return { location: `${origin}/` };
  const done = (ok: boolean) => ({ location: `${origin}/${proof.slug}?setup=${provider}-${ok ? "connected" : "failed"}#setup` });
  if (!input.session || input.session.slug !== proof.slug || input.session.role !== "owner") return done(false);
  const states = input.url.searchParams.getAll("state"), codes = input.url.searchParams.getAll("code"), errors = input.url.searchParams.getAll("error");
  if (states.length !== 1 || !states[0] || states[0].length > 512 || codes.length > 1 || errors.length > 1 || (codes[0] ?? "").length > 4096 || (errors[0] ?? "").length > 200) return done(false);
  if (!codes.length && !errors.length) return done(false);
  const body: Record<string, string> = { state: states[0] };
  if (codes.length) body.code = codes[0];
  if (errors.length) body.error = errors[0];
  try {
    const response = await input.backend(`/v1/onboarding/callback/${provider}`, { method: "POST", body: JSON.stringify({ ...body, redirect_uri: redirectUri(origin, provider), browser_proof: proof.proof }) });
    if (!response.ok || errors.length) return done(false);
    const setup = sanitizeSetup(await response.json().catch(() => null));
    const step = setup?.steps.find(candidate => candidate.id === OAUTH_STEP[provider]);
    return done(step?.state === "connected" || step?.state === "sandbox_connected" ||
      (provider === "slack" && step?.state === "needs_configuration" && step.display.authorized === true));
  } catch { return done(false); }
}

export const registrationRedirect = (slug: string) => `/${slug}#setup`;
/** Only a newly registered workspace opens setup; ordinary Google sign-in and linking keep their old targets. */
export function googleSignInRedirect(origin: string, slug: string, flow: { registered: boolean; linked: boolean }) {
  return `${origin}/${slug}${flow.registered ? "#setup" : flow.linked ? "?auth=linked" : ""}`;
}
