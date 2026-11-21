"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

const SCENARIOS: [string, string][] = [
  ["files", "Invoice in files"], ["email", "Invoice in email"], ["chat_refund", "Explained in chat"],
  ["nowhere", "Nowhere → ask on Slack"], ["overspend_chat", "Overspend approved in chat"],
  ["ksef", "Invoice only in KSeF"],
];

/** Dev-only (SCOUT_DEMO=1): posts scenario payments to the bank mock through the API. */
export function DemoPanel() {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [refreshing, startTransition] = useTransition();
  async function play(scenario: string, label: string) {
    setBusy(scenario); setMessage(null);
    const response = await fetch("/api/demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario }) }).catch(() => null);
    setBusy(null);
    setMessage(response?.ok ? `Played “${label}”. Scout picks it up within seconds; watch Tasks.` : "The demo scenario could not be started.");
    startTransition(() => router.refresh());
  }
  return <section aria-label="Demo scenarios" className="rounded-xl border border-dashed p-3">
    <p className="mb-2 font-mono text-[11px] uppercase tracking-wide text-muted-foreground">Demo · SCOUT_DEMO=1</p>
    <div className="flex flex-wrap gap-2">{SCENARIOS.map(([scenario, label]) =>
      <Button key={scenario} id={`scout-demo-${scenario}`} size="sm" variant="outline" disabled={busy !== null} aria-busy={busy === scenario} onClick={() => play(scenario, label)} className="relative">
        {/* Same width while pending: the label stays, a spinner overlays it. */}
        <span className={busy === scenario ? "invisible" : undefined}>{label}</span>
        {busy === scenario && <span className="absolute inset-0 flex items-center justify-center gap-1.5"><LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />Playing…</span>}
      </Button>)}</div>
    <p role="status" className="mt-2 min-h-4 text-xs text-muted-foreground">{message}{refreshing && message ? " Refreshing…" : ""}</p>
  </section>;
}
