// Browser-side helpers for workspace setup: wire parsing, requests and pure UI rules. No credentials are stored here.
import type { NetworkAnswers, Setup, SetupHandoff, SetupStatus, SetupStep, SetupStepId, SetupStepState, SetupUpdate } from "./onboarding-model.ts";

export type Fetcher = (path: string, init?: RequestInit) => Promise<Response>;
export type OAuthProvider = "google" | "slack";
export type Provider = "bunq" | "builtin" | "ksef" | OAuthProvider;

export const STEPS: { id: SetupStepId; title: string; description: string; cta: string; provider: Provider | null }[] = [
  { id: "bank", title: "Bank", description: "Read-only bunq sandbox account. No transfers, no production banking.", cta: "Connect sandbox bank", provider: "bunq" },
  { id: "invoicing", title: "Invoicing", description: "Scout's built-in invoicing for this workspace. It starts empty.", cta: "Use built-in invoicing", provider: "builtin" },
  { id: "ksef", title: "KSeF", description: "National e-invoice system, test environment only.", cta: "Set up test environment", provider: "ksef" },
  { id: "email", title: "Email", description: "Gmail / Google Workspace: Google grants read-only mailbox permission; Scout restricts reads to one label. Google sign-in alone is not mailbox consent. Disconnect or failed-setup revocation may affect other Scout workspaces using the same Google account.", cta: "Continue with Google", provider: "google" },
  { id: "slack", title: "Slack", description: "Install Scout in your Slack workspace. Scout does not send messages to verify it.", cta: "Connect workspace", provider: "slack" },
  { id: "network", title: "Company network", description: "Tell us whether Scout needs private-network access. An administrator sets it up.", cta: "Answer a few questions", provider: null },
];
const IDS = STEPS.map(entry => entry.id);
const STATES: SetupStepState[] = ["not_started", "needs_configuration", "connecting", "sandbox_connected", "connected", "failed", "skipped"];
const STATUSES: SetupStatus[] = ["not_started", "pending", "in_progress", "dismissed", "completed"];

export class OnboardingError extends Error {
  readonly status: number;
  readonly code?: string;
  constructor(message: string, status = 0, code?: string) { super(message); this.name = "OnboardingError"; this.status = status; this.code = code; }
  get conflict() { return this.status === 409 && this.code !== "already_connected"; }
}

const DEFAULT_MESSAGE: Record<number, string> = {
  401: "Your session has ended. Sign in again.", 403: "Only workspace owners can change setup.",
  409: "Setup was changed elsewhere.", 429: "Too many requests. Wait a moment and try again.",
};
async function failure(response: Response): Promise<OnboardingError> {
  let message = "", code: string | undefined;
  try { const body = await response.json() as { error?: unknown; message?: unknown; code?: unknown }; const text = body.error ?? body.message; if (typeof text === "string") message = text.slice(0, 240); if (body.code === "already_connected") code = body.code; } catch { /* not JSON */ }
  return new OnboardingError(message || DEFAULT_MESSAGE[response.status] || "Setup is unavailable right now. Try again shortly.", response.status, code);
}
async function request(fetcher: Fetcher, path: string, init: RequestInit = {}): Promise<unknown> {
  let response: Response;
  try { response = await fetcher(path, init); } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new OnboardingError("Could not reach Scout. Check your connection and try again.");
  }
  if (!response.ok) throw await failure(response);
  try { return await response.json(); } catch { throw new OnboardingError("Scout returned an unreadable response.", response.status); }
}
const send = (method: string, body?: unknown): RequestInit => body === undefined ? { method } : { method, headers: { "content-type": "application/json" }, body: JSON.stringify(body) };
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

export function parseSetup(value: unknown): Setup {
  const bad = () => new OnboardingError("Scout returned an unexpected setup response.");
  if (!record(value) || value.version !== 1 || !STATUSES.includes(value.status as SetupStatus) || typeof value.revision !== "number" || !Array.isArray(value.steps)) throw bad();
  const list = value.steps as unknown[];
  const steps = IDS.map(id => {
    const found = list.find((entry): entry is Record<string, unknown> => record(entry) && entry.id === id);
    if (!found || !STATES.includes(found.state as SetupStepState)) throw bad();
    const display: SetupStep["display"] = {};
    if (record(found.display)) for (const [key, item] of Object.entries(found.display)) if (item === null || typeof item === "string" || typeof item === "boolean") display[key] = item;
    return { id, state: found.state as SetupStepState, provider: typeof found.provider === "string" ? found.provider : null, message: typeof found.message === "string" ? found.message : null, display };
  });
  const network = record(value.network) ? value.network as NetworkAnswers : null;
  return { version: 1, status: value.status as SetupStatus, revision: value.revision, steps, network };
}

export const fetchSetup = async (fetcher: Fetcher, signal?: AbortSignal) => parseSetup(await request(fetcher, "/api/onboarding", { method: "GET", signal }));
export const patchSetup = async (fetcher: Fetcher, update: SetupUpdate) => parseSetup(await request(fetcher, "/api/onboarding", send("PATCH", update)));
export const connectProvider = async (fetcher: Fetcher, provider: Exclude<Provider, OAuthProvider>, body: Record<string, string>) =>
  parseSetup(await request(fetcher, `/api/onboarding/connect/${provider}`, send("POST", body)));
export const disconnectProvider = async (fetcher: Fetcher, provider: Provider) => parseSetup(await request(fetcher, `/api/onboarding/connect/${provider}`, send("DELETE", {})));
export function parseSlackChannelIds(value: string): string[] | null {
  const channels = [...new Set(value.split(/[\s,]+/).filter(Boolean))];
  return channels.length <= 20 && channels.every(id => /^[CG][A-Z0-9]{8,14}$/.test(id)) ? channels : null;
}
export const saveSlackChannels = async (fetcher: Fetcher, channels: string[]) => parseSetup(await request(fetcher, "/api/onboarding/slack/channels", send("PUT", { channels })));

/** Only https (or loopback http for local development) may be navigated to. */
export function safeAuthorizationUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || (url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))) return value;
  } catch { /* invalid */ }
  return null;
}
/** The server owns the redirect URI, state and browser proof. It answers with the provider URL, or (missing app credentials,
 *  prerequisites) with a Setup whose step explains why nothing was started. Google may carry a mail label. */
export async function startOAuth(fetcher: Fetcher, provider: OAuthProvider, options: { label?: string } = {}): Promise<{ url: string } | { setup: Setup }> {
  const result = await request(fetcher, `/api/onboarding/connect/${provider}`, send("POST", options.label ? { label: options.label } : {}));
  if (record(result) && "url" in result) {
    const url = safeAuthorizationUrl(result.url);
    if (!url) throw new OnboardingError("Scout did not return a safe authorization link. Nothing was connected.");
    return { url };
  }
  return { setup: parseSetup(result) };
}

export async function createHandoff(fetcher: Fetcher): Promise<SetupHandoff> {
  const value = await request(fetcher, "/api/onboarding/handoff", send("POST", {}));
  if (!record(value) || typeof value.setupUrl !== "string" || typeof value.markdown !== "string" || typeof value.prompt !== "string" || !record(value.manifest)) throw new OnboardingError("Scout returned an unexpected handoff.");
  const claudeUrl = typeof value.claudeUrl === "string" && value.claudeUrl.startsWith("claude-cli://") && value.claudeUrl.length < 5000 ? value.claudeUrl : null;
  return { setupUrl: value.setupUrl, markdown: value.markdown, manifest: value.manifest, prompt: value.prompt, claudeUrl };
}
export const handoffFiles = (handoff: SetupHandoff) => [
  { name: "scout-setup.md", mime: "text/markdown", content: handoff.markdown },
  { name: "scout-setup.json", mime: "application/json", content: JSON.stringify(handoff.manifest, null, 2) },
];

export type Tone = "muted" | "primary" | "warning" | "danger";
export function stepStatus(step: SetupStep): { label: string; tone: Tone } {
  if (step.display.revocation_unconfirmed === true) return { label: "Provider revocation unconfirmed", tone: "warning" };
  switch (step.state) {
    case "connected": return { label: "Connected", tone: "primary" };
    case "sandbox_connected": return { label: "Sandbox connected", tone: "primary" };
    case "needs_configuration": return { label: step.id === "network" ? "Administrator setup required" : "Needs setup", tone: "warning" };
    case "connecting": return { label: "Connecting", tone: "muted" };
    case "failed": return { label: "Failed", tone: "danger" };
    case "skipped": return { label: "Skipped", tone: "muted" };
    default: return { label: "Not started", tone: "muted" };
  }
}

export const canEdit = (role: string | undefined) => role === "owner";
export function shouldOpenSetup(input: { hash: string; role: string | undefined; status: SetupStatus }) {
  return !input.hash.replace(/^#/, "") && canEdit(input.role) && (input.status === "pending" || input.status === "in_progress");
}
const SETTLED: SetupStepState[] = ["connected", "sandbox_connected", "skipped"];
export const canComplete = (setup: Setup) => setup.steps.every(step => SETTLED.includes(step.state));
export const completionAction = (setup: Setup): "complete" | "skip" => canComplete(setup) ? "complete" : "skip";

// Sequential wizard: one step is shown at a time. These are pure so selection and navigation are testable without a browser.
export const isSetupStepId = (value: unknown): value is SetupStepId => typeof value === "string" && (IDS as string[]).includes(value);
const CONNECTED: SetupStepState[] = ["connected", "sandbox_connected"];
const unsettled = (setup: Setup) => setup.steps.find(step => !SETTLED.includes(step.state))?.id;
/** Resume point: the first step that is not connected, sandbox-connected or skipped; a fully settled setup shows the last step. */
export const firstUnsettledStep = (setup: Setup): SetupStepId => unsettled(setup) ?? IDS[IDS.length - 1];
/** An explicit, known and present step (route or OAuth return) wins, even when earlier steps are unsettled; otherwise resume. */
export const resolveStep = (setup: Setup, requested?: SetupStepId | null): SetupStepId =>
  requested && isSetupStepId(requested) && setup.steps.some(step => step.id === requested) ? requested : firstUnsettledStep(setup);
/** The OAuth callback redirects with ?setup=<provider>-connected|failed; the provider decides which step to show again. */
export const callbackStep = (value: string | null | undefined): SetupStepId | undefined => {
  const provider = /^(google|slack)-(connected|failed)$/.exec(value ?? "")?.[1];
  return provider === "google" ? "email" : provider === "slack" ? "slack" : undefined;
};
export const stepProgress = (id: SetupStepId) => ({ number: IDS.indexOf(id) + 1, total: IDS.length, label: `Step ${IDS.indexOf(id) + 1} of ${IDS.length}` });
export const adjacentStep = (id: SetupStepId, delta: 1 | -1): SetupStepId | null => IDS[IDS.indexOf(id) + delta] ?? null;
/** What Next does to the current step: move on, or first record an explicit skip. Saved or in-flight work is never overwritten by a skip. */
export function nextStepAction(step: SetupStep): "advance" | "skip" {
  if (SETTLED.includes(step.state) || step.state === "connecting") return "advance";
  if (step.id === "network" && step.state === "needs_configuration") return "advance";
  if (step.id === "slack" && step.display.authorized === true) return "advance";
  return "skip";
}
export const validNip =(value: string) => /^\d{10}$/.test(value);

export type NetworkDraft = { required: boolean; provider: string; protocol: string; administrator: string; systems: string; companyOnly: boolean };
const SECRET = /private\s*key|BEGIN [A-Z ]*(KEY|CERTIFICATE)|pass(word|phrase)?\s*[:=]|secret\s*[:=]|token\s*[:=]|api[_ -]?key\s*[:=]|\.(ovpn|conf|pem|key)\b/i;
export function buildNetworkAnswers(draft: NetworkDraft): { ok: true; answers: NetworkAnswers } | { ok: false; errors: Record<string, string> } {
  if (!draft.required) return { ok: true, answers: { required: false, provider: "", protocol: "", administrator: "", systems: [], company_only: false } };
  const errors: Record<string, string> = {};
  const text = (field: "provider" | "protocol" | "administrator", label: string) => {
    const value = draft[field].trim();
    if (!value) errors[field] = `Enter ${label}.`;
    else if (value.length > 120) errors[field] = "Keep this under 120 characters.";
    else if (SECRET.test(value)) errors[field] = "Do not enter keys, passwords or VPN profiles here.";
    return value;
  };
  const provider = text("provider", "the VPN provider"), protocol = text("protocol", "the protocol"), administrator = text("administrator", "the responsible administrator");
  const systems = [...new Set(draft.systems.split(/[\n,]/).map(item => item.trim()).filter(Boolean))];
  if (systems.length === 0) errors.systems = "List at least one system Scout should reach.";
  else if (systems.length > 20) errors.systems = "List at most 20 systems.";
  else if (systems.some(item => item.length > 80 || SECRET.test(item))) errors.systems = "Use short system names, without keys or passwords.";
  if (!draft.companyOnly) errors.companyOnly = "Confirm that access is limited to company folders and mail.";
  return Object.keys(errors).length ? { ok: false, errors } : { ok: true, answers: { required: true, provider, protocol, administrator, systems, company_only: true } };
}
