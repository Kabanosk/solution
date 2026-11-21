/** The per-workspace NeMo guardrails switch (T084): shared by the API route, the store and the Audit page. */
export type GuardrailsSettings = { enabled: boolean; updatedBy: string | null; updatedAt: string | null };

/** No row means ON: the worker and the api treat a missing or unreadable switch the same way. */
export const GUARDRAILS_DEFAULT: GuardrailsSettings = { enabled: true, updatedBy: null, updatedAt: null };

export function parseGuardrailsToggle(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value) || typeof (value as { enabled?: unknown }).enabled !== "boolean")
    throw new Error("Choose whether the guardrails are on.");
  return (value as { enabled: boolean }).enabled;
}

/** The audit row of one change, the same shape `scout.guardrails.set_switch` writes. */
export function guardrailsAudit(enabled: boolean, who: string) {
  const state = enabled ? "ON" : "OFF";
  return { actor: "dashboard", requester: who, role: "owner", action: "guardrails_toggled", target: "guardrails:workspace",
    resource: `NeMo guardrails turned ${state}`, decision: "ALLOW", policy: "guardrails-switch", reason: state } as const;
}

/** Owners see the warning only when the switch is known to be OFF (unknown counts as ON, like the backend). */
export function guardrailsBanner(role: string | undefined, settings: GuardrailsSettings | null): boolean {
  return role === "owner" && settings?.enabled === false;
}

/** How often a visible page re-reads the switch, so an OFF set elsewhere (another owner, `guard-reset`) shows up. */
export const GUARDRAILS_POLL_MS = 15000;
type Events = { addEventListener(type: string, listener: () => void): void; removeEventListener(type: string, listener: () => void): void };
export type WatchEnv = { doc: Events & { visibilityState: string }; win: Events; setInterval(fn: () => void, ms: number): unknown; clearInterval(id: unknown): void };

/** Calls `refresh` every GUARDRAILS_POLL_MS while the page is visible, on window focus and when the tab becomes visible
 * again. Returns the function that stops it. */
export function watchGuardrails(refresh: () => void, env: WatchEnv): () => void {
  const tick = () => { if (env.doc.visibilityState === "visible") refresh(); };
  const timer = env.setInterval(tick, GUARDRAILS_POLL_MS);
  env.doc.addEventListener("visibilitychange", tick);
  env.win.addEventListener("focus", tick);
  return () => {
    env.clearInterval(timer);
    env.doc.removeEventListener("visibilitychange", tick);
    env.win.removeEventListener("focus", tick);
  };
}
