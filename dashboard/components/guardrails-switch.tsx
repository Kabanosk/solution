"use client";

import { useEffect, useSyncExternalStore } from "react";
import { ShieldCheck, ShieldOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { guardrailsBanner, watchGuardrails, type GuardrailsSettings } from "@/lib/guardrails-model";
import { fullTimestamp } from "@/lib/scout-model";
import { tenantFetch } from "@/lib/tenant-browser";
import { cn } from "@/lib/utils";

// One shared copy of the switch for this page: the Audit switch and the warning banner always agree.
type State = { settings: GuardrailsSettings | null; busy: boolean; error: string };
const INITIAL: State = { settings: null, busy: false, error: "" };
let state: State = INITIAL;
let started = false;
const listeners = new Set<() => void>();
const update = (patch: Partial<State>) => { state = { ...state, ...patch }; listeners.forEach(listener => listener()); };
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };

let loading = false;
let writes = 0; // a read that started before a toggle must not overwrite the toggle's answer
async function load() {
  if (loading) return;
  loading = true;
  const seen = writes;
  try {
    const response = await tenantFetch("/api/guardrails");
    if (!response.ok) throw new Error();
    const settings = await response.json() as GuardrailsSettings;
    if (seen === writes) update({ settings, error: "" });
  } catch { update({ error: "The guardrails switch could not be loaded." }); }
  finally { loading = false; }
}

/** True when the change was saved. */
export async function setGuardrails(enabled: boolean): Promise<boolean> {
  writes++;
  update({ busy: true, error: "" });
  try {
    const response = await tenantFetch("/api/guardrails", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error);
    update({ settings: value as GuardrailsSettings });
    return true;
  } catch (error) {
    update({ error: error instanceof Error && error.message ? error.message : "Could not change the guardrails switch." });
    return false;
  } finally { update({ busy: false }); }
}

/** Owners only: loads once, then re-reads every 15 s while visible, on focus and when the tab comes back, so an OFF
 * set in another session or a `guard-reset` shows up without a reload. One watcher per page. */
function useGuardrails(owner: boolean): State {
  const snapshot = useSyncExternalStore(subscribe, () => state, () => INITIAL);
  useEffect(() => {
    if (!owner || started) return;
    started = true;
    void load();
    watchGuardrails(() => { if (!state.busy) void load(); }, { doc: document, win: window, setInterval: (fn, ms) => window.setInterval(fn, ms), clearInterval: id => window.clearInterval(id as number) });
  }, [owner]);
  return owner ? snapshot : INITIAL;
}

/** The switch in the Audit page header. `onChanged` runs after a saved toggle (the page reloads its audit rows). */
export function GuardrailsSwitch({ owner, onChanged }: { owner: boolean; onChanged?: () => void }) {
  const { settings, busy, error } = useGuardrails(owner);
  if (!owner) return null;
  const on = settings?.enabled ?? true;
  const Icon = on ? ShieldCheck : ShieldOff;
  const since = settings?.updatedAt ? fullTimestamp(settings.updatedAt).replace(" (Rome)", "") : null;
  return <section id="scout-guardrails" aria-labelledby="scout-guardrails-label" className="flex items-center justify-between gap-4 rounded-xl border bg-card px-4 py-3">
    <div className="flex min-w-0 items-start gap-3">
      <Icon className={cn("mt-0.5 size-4 shrink-0", on ? "text-primary" : "text-warning")} aria-hidden="true" />
      <div className="min-w-0">
        <label id="scout-guardrails-label" htmlFor="scout-guardrails-switch" className="text-sm font-medium">NeMo Guardrails</label>
        <p id="scout-guardrails-help" className="text-xs leading-5 text-muted-foreground">Checks every message, tool result and reply</p>
        {!on && <p id="scout-guardrails-state" className="text-xs leading-5 text-warning">Off{since ? ` since ${since}` : ""}{settings?.updatedBy ? ` · turned off by ${settings.updatedBy}` : ""}. Access controls stay on.</p>}
        {error && <p id="scout-guardrails-error" role="alert" className="text-xs leading-5 text-danger">{error}</p>}
      </div>
    </div>
    <div className="flex shrink-0 items-center gap-2">
      <span className="text-xs text-muted-foreground" aria-hidden="true">{settings ? (on ? "On" : "Off") : "…"}</span>
      <Switch id="scout-guardrails-switch" checked={on} disabled={!settings || busy} aria-describedby="scout-guardrails-help" onCheckedChange={checked => void setGuardrails(checked).then(saved => { if (saved) onChanged?.(); })} />
    </div>
  </section>;
}

/** The warning every owner sees on every page while the switch is OFF. */
export function GuardrailsBanner({ role }: { role: string | undefined }) {
  const { settings, busy } = useGuardrails(role === "owner");
  if (!guardrailsBanner(role, settings)) return null;
  return <div id="scout-guardrails-off" role="status" className="mb-5 flex shrink-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-warning-border bg-warning-muted px-4 py-3 text-sm">
    <ShieldOff className="size-4 shrink-0 text-warning" aria-hidden="true" />
    <div className="min-w-0 flex-1">
      <p className="font-medium">Guardrails are off for this workspace</p>
      <p className="text-xs leading-5 text-muted-foreground">Messages, tool results and replies are not checked. Access controls stay on.</p>
    </div>
    <Button id="scout-guardrails-turn-on" size="sm" variant="outline" className="h-8 gap-2 bg-card" disabled={busy} onClick={() => void setGuardrails(true)}>
      <ShieldCheck className="size-3.5 text-primary" aria-hidden="true" />{busy ? "Turning on…" : "Turn back on"}
    </Button>
  </div>;
}
