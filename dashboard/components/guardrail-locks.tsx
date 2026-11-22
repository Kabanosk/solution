"use client";

import { useCallback, useEffect, useState } from "react";
import { LockKeyhole, LockOpen } from "lucide-react";
import { Button } from "@/components/ui/button";
import { watchGuardrails, type GuardrailLock } from "@/lib/guardrails-model";
import { tenantFetch } from "@/lib/tenant-browser";

const until = (iso: string) => new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Rome" }).format(new Date(iso)) + " (Rome)";

/** Owners only: the people paused after three blocked messages, each with an Unlock button. Renders nothing while
 * nobody is locked (or while the list cannot be read: the switch next to it keeps working). `onChanged` runs after an
 * unlock so the page reloads its audit rows. */
export function GuardrailLocks({ owner, onChanged }: { owner: boolean; onChanged?: () => void }) {
  const [locks, setLocks] = useState<GuardrailLock[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await tenantFetch("/api/guardrails/locks");
      if (!response.ok) throw new Error();
      setLocks(await response.json() as GuardrailLock[]);
    } catch { setLocks([]); }
  }, []);

  useEffect(() => {
    if (!owner) return;
    void load();
    return watchGuardrails(() => { void load(); }, { doc: document, win: window, setInterval: (fn, ms) => window.setInterval(fn, ms), clearInterval: id => window.clearInterval(id as number) });
  }, [owner, load]);

  async function unlock(lock: GuardrailLock) {
    setBusy(lock.person);
    setError("");
    try {
      const response = await tenantFetch("/api/guardrails/locks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ person: lock.person }) });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error);
      setLocks(value.locks as GuardrailLock[]);
      onChanged?.();
    } catch (failure) {
      setError(failure instanceof Error && failure.message ? failure.message : `Could not unlock ${lock.name}.`);
    } finally { setBusy(null); }
  }

  if (!owner || (!locks.length && !error)) return null;
  return <section id="scout-guardrail-locks" aria-labelledby="scout-guardrail-locks-label" className="rounded-xl border bg-card">
    <div className="flex items-start gap-3 px-4 py-3">
      <LockKeyhole className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
      <div className="min-w-0">
        <h2 id="scout-guardrail-locks-label" className="text-sm font-medium">Paused after repeated blocked requests</h2>
        <p className="text-xs leading-5 text-muted-foreground">Three blocked messages in 7 days pause a person's access to Scout for 24 hours. Compliance was notified.</p>
        {error && <p id="scout-guardrail-locks-error" role="alert" className="text-xs leading-5 text-danger">{error}</p>}
      </div>
    </div>
    <ul className="divide-y border-t">
      {locks.map(lock => <li key={lock.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{lock.name}</p>
          <p className="text-xs leading-5 text-muted-foreground">Paused until <time dateTime={lock.lockedUntil}>{until(lock.lockedUntil)}</time>{lock.rails && <> · <span className="font-mono">{lock.rails}</span></>}</p>
        </div>
        <Button size="sm" variant="outline" className="h-8 shrink-0 gap-2" disabled={busy !== null} aria-label={`Unlock ${lock.name}`} onClick={() => void unlock(lock)}>
          <LockOpen className="size-3.5" aria-hidden="true" />{busy === lock.person ? "Unlocking…" : "Unlock"}
        </Button>
      </li>)}
    </ul>
  </section>;
}
