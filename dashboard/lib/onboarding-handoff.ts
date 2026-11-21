// Agent-neutral setup handoff. Built only from an explicit allowlist: public origin, workspace slug and per-step
// state and a boolean revocation warning. Never include provider diagnostics, network answers or other display data.
import { validSlug } from "./auth-crypto.ts";
import type { Setup, SetupHandoff, SetupStepId, SetupStepState } from "./onboarding-model.ts";

const CLAUDE_URL_LIMIT = 5000;
const STEPS: { id: SetupStepId; label: string; action: string }[] = [
  { id: "bank", label: "Bank (bunq sandbox, read-only)", action: "Open Scout setup and choose Connect sandbox bank. The owner may paste a bunq sandbox API key into Scout itself, or leave it empty to create a sandbox account." },
  { id: "invoicing", label: "Invoicing (built-in)", action: "Open Scout setup and choose Use built-in invoicing." },
  { id: "ksef", label: "KSeF (test environment only)", action: "Open Scout setup and enter the company NIP for the KSeF test environment. Production tax submission is out of scope." },
  { id: "email", label: "Email (Gmail / Google Workspace)", action: "The owner opens Scout setup and chooses Continue with Google, then grants read-only mailbox access in Google's own consent screen." },
  { id: "slack", label: "Slack", action: "The owner opens Scout setup and chooses Connect workspace, then approves the Slack installation." },
  { id: "network", label: "Company network", action: "The owner answers the network questions in Scout setup. An administrator must arrange any private access; Scout does not install VPNs." },
];
const STATE_TEXT: Record<SetupStepState, string> = {
  not_started: "Not configured yet.",
  needs_configuration: "Needs configuration: a prerequisite or authorization is still missing.",
  connecting: "Authorization is in progress; wait for it to finish.",
  sandbox_connected: "Connected to a sandbox only; this is not a production connection.",
  connected: "Connected.",
  failed: "Failed; the owner can retry it in Scout setup.",
  skipped: "Skipped by the owner.",
};
const STATES = Object.keys(STATE_TEXT) as SetupStepState[];
const DONE: SetupStepState[] = ["connected", "sandbox_connected", "skipped"];

function checkedOrigin(origin: string) {
  let url: URL;
  try { url = new URL(origin); } catch { throw new Error("Invalid origin."); }
  const loopback = url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname);
  if (url.origin !== origin) throw new Error("Invalid origin: expected a bare origin.");
  if (url.protocol !== "https:" && !loopback) throw new Error("The setup link must use https.");
  return url.origin;
}

export function buildHandoff(input: { origin: string; slug: string; setup: Setup | null }): SetupHandoff {
  const origin = checkedOrigin(input.origin);
  if (!validSlug(input.slug)) throw new Error("Invalid workspace slug.");
  const setupUrl = `${origin}/${input.slug}#setup`;
  const reported = new Map<string, SetupStepState>();
  const unconfirmed = new Set<string>();
  for (const step of input.setup?.steps ?? []) if (typeof step?.id === "string" && STATES.includes(step.state)) {
    reported.set(step.id, step.state);
    if (step.display?.revocation_unconfirmed === true) unconfirmed.add(step.id);
  }
  const steps = STEPS.map(step => {
    const state = reported.get(step.id) ?? "not_started";
    const warning = unconfirmed.has(step.id);
    return { id: step.id, label: step.label, state, status_text: STATE_TEXT[state],
      ...(warning ? { revocation_unconfirmed: true } : {}),
      next_action: warning ? "Provider revocation is unconfirmed. The owner must review or revoke Scout access in the provider's own security settings before reconnecting." : DONE.includes(state) ? "No action needed." : step.action };
  });
  const manifest = {
    schema: "scout.setup/v1", app_origin: origin, workspace: { slug: input.slug }, setup_url: setupUrl, steps,
    rules: { modify_scout_code: false, authentication: "The person signs in to Scout themselves and gives provider consent in Scout's own UI.", secrets_in_chat: false, parallel: "Steps are independent and may be handled in parallel as separate bounded tasks." },
  };
  const list = steps.map(step => `- ${step.label}: ${step.state.replaceAll("_", " ")}. ${step.next_action}`).join("\n");
  const rules = [
    "Do not modify Scout's source code, styling or deployment. Only help configure this company's connections through Scout's own setup screen.",
    "The person must sign in to Scout with their own account and give any provider consent or authorization themselves. You cannot bypass sign-in, OAuth or workspace membership, and this link grants no access by itself.",
    "Never ask for or accept passwords, API keys, tokens or VPN profiles in chat; the person enters them directly in Scout. If you have no authenticated browser, give the person these steps to do by hand.",
    "The steps are independent. They may be handled in parallel as separate, bounded tasks, but keep each to its own step and finish by asking the person to check the result in Scout.",
    "Report states honestly: sandbox connections are not production, and skipped, failed or unconfigured steps stay that way until Scout shows them as connected.",
  ];
  const markdown = [
    "# Scout workspace setup", "",
    `Setup link (sign in required): ${setupUrl}`, "",
    "You are helping a person configure their company's connections in Scout. Everything is optional.", "",
    "## Rules", ...rules.map(rule => `- ${rule}`), "",
    "## Steps and current state", list, "",
    "A machine-readable version of this context uses the `scout.setup/v1` manifest.", "",
  ].join("\n");
  const prompt = [
    `Help me finish setting up my Scout workspace at ${setupUrl} (sign in required).`,
    "Do not modify Scout's source code or styling. I will sign in and give consent myself; never ask for passwords, API keys or tokens in chat.",
    "Unfinished steps (independent, can run in parallel):",
    ...steps.filter(step => !DONE.includes(step.state) || step.revocation_unconfirmed).map(step => `- ${step.label}: ${step.state.replaceAll("_", " ")}${step.revocation_unconfirmed ? ". Provider revocation is unconfirmed; review provider security settings before reconnecting." : ""}`),
    "Tell me honestly what is sandbox, skipped or still unconfigured.",
  ].join("\n");
  const claude = `claude-cli://open?q=${encodeURIComponent(prompt)}`;
  return { setupUrl, markdown, manifest, prompt, claudeUrl: claude.length < CLAUDE_URL_LIMIT ? claude : null };
}
