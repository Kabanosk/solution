"use client";

import { useEffect, useSyncExternalStore } from "react";
import { ShieldCheck, ShieldOff } from "lucide-react";
import { Switch } from "@/components/ui/switch";
import { watchGuardrails, type GuardrailsSettings } from "@/lib/guardrails-model";
import { fullTimestamp } from "@/lib/scout-model";
import { tenantFetch } from "@/lib/tenant-browser";
import { cn } from "@/lib/utils";

// One shared copy of the switch for this page.
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
  } catch { update({ error: "Could not load the guardrails switch." }); }
  finally { loading = false; }
}

/** True when the change was saved. */
async function setGuardrails(enabled: boolean): Promise<boolean> {
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

/** The compact switch in the chat's top row, owners only. Unknown (loading or unavailable) shows as ON, like the backend. */
export function GuardrailsSwitch({ owner }: { owner: boolean }) {
  const { settings, busy, error } = useGuardrails(owner);
  if (!owner) return null;
  const on = settings?.enabled ?? true;
  const Icon = on ? ShieldCheck : ShieldOff;
  const since = settings?.updatedAt ? fullTimestamp(settings.updatedAt).replace(" (Rome)", "") : null;
  const detail = on ? "Checks every message, tool result and reply"
    : `Off${since ? ` since ${since}` : ""}${settings?.updatedBy ? ` · turned off by ${settings.updatedBy}` : ""}. Messages are not checked; access controls stay on.`;
  return <div id="scout-guardrails" className="flex min-w-0 items-center gap-2" title={detail}>
    <Switch id="scout-guardrails-switch" checked={on} disabled={!settings || busy} aria-describedby="scout-guardrails-help"
      onCheckedChange={checked => void setGuardrails(checked)} />
    <label id="scout-guardrails-label" htmlFor="scout-guardrails-switch" className={cn("flex min-h-9 cursor-pointer items-center gap-1.5 text-xs select-none", on ? "text-muted-foreground" : "font-medium text-warning")}>
      <Icon className={cn("size-3.5 shrink-0", on ? "text-primary" : "text-warning")} aria-hidden="true" />
      <span>Guardrails {settings ? (on ? "on" : "off") : "…"}</span>
    </label>
    <span id="scout-guardrails-help" className="sr-only">{detail}</span>
    {error && <span id="scout-guardrails-error" role="alert" className="truncate text-xs text-danger">{error}</span>}
  </div>;
}
