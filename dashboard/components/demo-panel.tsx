"use client";

import { useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LoaderCircle, Play } from "lucide-react";
import { Button } from "@/components/ui/button";

const SCENARIOS: [string, string, string][] = [
  ["files", "Invoice in files", "A payment whose invoice sits on a company laptop."],
  ["email", "Invoice in email", "The invoice arrived as an email attachment."],
  ["chat_refund", "Explained in chat", "Someone already explained the payment on Slack."],
  ["nowhere", "Nowhere → ask on Slack", "No document anywhere: Scout asks the person who paid."],
  ["overspend_chat", "Overspend approved in chat", "A team goes over budget; the approval is in chat."],
  ["ksef", "Invoice only in KSeF", "The invoice exists only in the Polish e-invoicing system."],
];

/** Dev-only view (SCOUT_DEMO=1): posts scenario payments to the bank mock through the API. */
export function DemoView() {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [refreshing, startTransition] = useTransition();
  async function play(scenario: string, label: string) {
    setBusy(scenario); setMessage(null);
    const response = await fetch("/api/demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario }) }).catch(() => null);
    setBusy(null);
    setMessage(response?.ok ? { ok: true, text: `Played “${label}”. Scout picks it up within seconds.` } : { ok: false, text: "The demo scenario could not be started." });
    startTransition(() => router.refresh());
  }
  return <section id="scout-demo" aria-labelledby="scout-demo-title" className="max-w-3xl space-y-4">
    <div>
      <h1 id="scout-demo-title" className="text-xl font-medium tracking-tight">Demo scenarios</h1>
      <p className="mt-1 text-sm text-muted-foreground">Each scenario books one payment in the mocked bank. Scout then works it like a real one. Only shown with SCOUT_DEMO=1.</p>
    </div>
    <ul className="overflow-hidden rounded-xl border bg-card">
      {SCENARIOS.map(([scenario, label, description]) => <li key={scenario} className="flex items-center gap-4 border-b px-4 py-2.5 last:border-b-0">
        <span className="min-w-0 flex-1"><span className="block text-[13px] font-medium">{label}</span><span className="block text-xs text-muted-foreground">{description}</span></span>
        <Button id={`scout-demo-${scenario}`} size="sm" variant="outline" disabled={busy !== null} aria-busy={busy === scenario} onClick={() => play(scenario, label)} className="relative w-24 shrink-0">
          {/* Same width while pending: the label stays, a spinner overlays it. */}
          <span className={busy === scenario ? "invisible flex items-center gap-1.5" : "flex items-center gap-1.5"}><Play className="size-3.5" aria-hidden="true" />Play</span>
          {busy === scenario && <span className="absolute inset-0 flex items-center justify-center gap-1.5"><LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />Playing…</span>}
        </Button>
      </li>)}
    </ul>
    <p id="scout-demo-status" role="status" className={message?.ok === false ? "min-h-5 text-sm text-danger" : "min-h-5 text-sm text-muted-foreground"}>
      {message?.text}{refreshing && message ? " Refreshing…" : ""}
      {message?.ok && <> <Link href="/#tasks" className="text-foreground underline underline-offset-2">Watch it in Tasks</Link></>}
    </p>
  </section>;
}
