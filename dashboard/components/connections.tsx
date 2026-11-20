"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Building2, Check, Database, FileCheck2, HardDrive, LoaderCircle, Mail, MessagesSquare, X, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiHost, invoiceTotal, ksefSummary, parseKsefConfig } from "@/lib/ksef";
import { fullTimestamp, money, type Connection } from "@/lib/scout-model";

const CARDS: { id?: string; kind: Connection["kind"]; title: string; description: string; icon: LucideIcon }[] = [
  { kind: "bank", title: "Revolut Business", description: "Bank transactions · mocked", icon: Building2 },
  { id: "invoicing", kind: "invoicing", title: "Invoicing", description: "Invoices and POs · Postgres", icon: Database },
  { kind: "mail", title: "Email", description: "Company mailbox · Gmail, read-only", icon: Mail },
  { kind: "chat", title: "Slack", description: "Chat context, DMs and attachments", icon: MessagesSquare },
  { kind: "files", title: "Company laptops", description: "Authorized work folders over VPN", icon: HardDrive },
];

const statusOf = (connection: Connection | undefined) => !connection ? "not configured" : !connection.enabled ? "disabled" : connection.status;

function StatusBadge({ status }: { status: string }) {
  return <Badge variant="outline" className={status === "connected" ? "border-success-border font-normal text-success" : status === "error" ? "border-danger-border font-normal text-danger" : "font-normal text-muted-foreground"}>
    <span className={`mr-1 inline-block size-1.5 rounded-full ${status === "connected" ? "bg-emerald-500" : status === "error" ? "bg-red-500" : "bg-muted-foreground/50"}`} aria-hidden="true" />{status}</Badge>;
}

const POLL_MS = 1500;
const POLL_LIMIT_MS = 120_000;
const MAX_ROWS = 8;
const errorMessage = (payload: unknown, fallback: string) => {
  if (typeof payload !== "object" || payload === null || !("error" in payload)) return fallback;
  const error = payload.error;
  if (typeof error === "string") return error;
  if (typeof error === "object" && error !== null && "message" in error && typeof error.message === "string") return error.message;
  return fallback;
};

function KsefCard({ connection }: { connection: Connection | undefined }) {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  // Set from the click until the refreshed row reports a new state (the row can still show the previous run).
  const [waiting, setWaiting] = useState<{ since: number; baseline: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const config = parseKsefConfig(connection?.config);
  const signature = `${connection?.lastSyncAt ?? ""}|${config.steps.length}|${config.phase}`;
  const status = statusOf(connection);
  const running = config.phase === "running";
  const polling = running || waiting !== null;

  useEffect(() => {
    if (!waiting) return;
    if (running || (config.phase !== "idle" && signature !== waiting.baseline) || Date.now() - waiting.since > POLL_LIMIT_MS) setWaiting(null);
  }, [waiting, running, config.phase, signature]);

  useEffect(() => {
    if (!polling) return;
    const timer = setInterval(() => router.refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [polling, router]);

  async function connect() {
    setSubmitting(true); setError(null);
    try {
      const response = await fetch("/api/ksef", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const payload: unknown = await response.json().catch(() => null);
      const conflict = response.status === 409;
      if (!response.ok && !conflict) setError(errorMessage(payload, "KSeF could not be reached. Try again."));
      else {
        if (conflict) setError(errorMessage(payload, "A KSeF run is already in progress."));
        setWaiting({ since: Date.now(), baseline: signature });
        router.refresh();
      }
    } catch {
      setError("KSeF could not be reached. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const busy = submitting || polling;
  const host = apiHost(config.api);
  const rows = config.invoices.slice(0, MAX_ROWS);
  return <Card id="connection-ksef" className="gap-3 shadow-none xl:col-span-2">
    <CardHeader className="flex flex-row items-start justify-between gap-3">
      <div className="flex items-center gap-3"><span className="flex size-8 items-center justify-center rounded-md border text-muted-foreground"><FileCheck2 className="size-4" aria-hidden="true" /></span>
        <div><CardTitle className="text-sm font-medium">KSeF</CardTitle><CardDescription className="text-xs">National e-invoice system · test environment</CardDescription></div></div>
      <StatusBadge status={status} />
    </CardHeader>
    <CardContent className="space-y-3 text-xs text-muted-foreground">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          {(config.nip || host) && <p id="ksef-target">{config.nip && <>NIP <span className="font-mono">{config.nip}</span></>}{config.nip && host && " · "}{host && <span className="font-mono">{host}</span>}</p>}
          <p id="ksef-last-sync">Last sync: {connection?.lastSyncAt ? fullTimestamp(connection.lastSyncAt) : "never"}</p>
        </div>
        <Button id="ksef-connect" size="sm" disabled={busy} onClick={connect}>
          {busy && <LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />}
          {busy ? "Syncing…" : status === "connected" ? "Sync again" : "Connect KSeF"}</Button>
      </div>
      {error && <p id="ksef-error" role="alert" className="text-danger">{error}</p>}
      {connection?.lastError && !error && <p id="ksef-last-error" className="text-danger">{connection.lastError}</p>}
      {config.steps.length > 0 && <ol id="ksef-steps" aria-label="KSeF sync steps" className="space-y-1 rounded-md border px-3 py-2">
        {config.steps.map((entry, index) => {
          const last = index === config.steps.length - 1;
          return <li key={`${index}-${entry.at ?? ""}`} className="flex items-start gap-2">
            {last && running ? <LoaderCircle className="mt-0.5 size-3 shrink-0 motion-safe:animate-spin" aria-label="In progress" />
              : entry.ok ? <Check className="mt-0.5 size-3 shrink-0 text-success" aria-label="Done" /> : <X className="mt-0.5 size-3 shrink-0 text-danger" aria-label="Failed" />}
            <span className={entry.ok ? "text-foreground" : "text-danger"}>{entry.text}</span></li>;
        })}
      </ol>}
      {config.phase === "done" && <div className="space-y-2">
        <p id="ksef-summary" className="text-[13px] text-foreground">{ksefSummary(config, money)}</p>
        {rows.length > 0 && <Table id="ksef-invoices" className="text-xs">
          <TableHeader><TableRow><TableHead>Invoice</TableHead><TableHead>Supplier</TableHead><TableHead className="text-right">Total</TableHead><TableHead>KSeF number</TableHead><TableHead>Matched</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(entry => <TableRow key={`${entry.number}-${entry.ksefNumber}`}>
            <TableCell className="font-mono">{entry.number}</TableCell>
            <TableCell>{entry.supplier || "—"}</TableCell>
            <TableCell className="text-right tabular-nums">{invoiceTotal(entry, money)}</TableCell>
            <TableCell className="max-w-40 truncate font-mono" title={entry.ksefNumber}>{entry.ksefNumber || "—"}</TableCell>
            <TableCell className={entry.linked ? "text-success" : "text-muted-foreground"}>{entry.linked ? "yes" : "no"}</TableCell>
          </TableRow>)}</TableBody>
        </Table>}
        {config.invoices.length > rows.length && <p>{config.invoices.length - rows.length} more not shown.</p>}
      </div>}
    </CardContent>
  </Card>;
}

const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

export function Connections({ connections }: { connections: Connection[] }) {
  const [toast, setToast] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  useEffect(() => { // Back from Google via the back button (bfcache): the link works again.
    const reset = () => setConnecting(false);
    window.addEventListener("pageshow", reset);
    return () => window.removeEventListener("pageshow", reset);
  }, []);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const google = params.get("google");
    if (!google) return;
    setToast(google === "connected" ? "Google connected. Scout can now read the authorized mail label." : "Google connection failed. Try again.");
    params.delete("google");
    window.history.replaceState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}${window.location.hash}`);
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, []);

  return <section className="space-y-4">
    {toast && <div role="status" className="fixed top-4 right-4 z-50 rounded-lg border border-success-border bg-success-muted px-4 py-3 text-sm text-success shadow-sm">{toast}</div>}
    <div><h1 className="text-2xl font-medium tracking-tight">Connections</h1>
      <p className="mt-1 text-sm text-muted-foreground">Scoped, read-only sources Scout uses. Status and last sync come from the worker.</p></div>
    <div className="grid gap-4 md:grid-cols-2 xl:grid-flow-dense xl:grid-cols-3">{CARDS.flatMap(({ id, kind, title, description, icon: Icon }) => {
      const connection = connections.find(entry => id ? entry.id === id : entry.kind === kind);
      const status = statusOf(connection);
      const granted = strings(connection?.config.scopes);
      const missing = strings(connection?.config.missing_scopes);
      const card = <Card key={kind} className="gap-3 shadow-none">
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div className="flex items-center gap-3"><span className="flex size-8 items-center justify-center rounded-md border text-muted-foreground"><Icon className="size-4" aria-hidden="true" /></span>
            <div><CardTitle className="text-sm font-medium">{connection?.displayName && connection.displayName !== title ? `${title} · ${connection.displayName}` : title}</CardTitle><CardDescription className="text-xs">{description}</CardDescription></div></div>
          <StatusBadge status={status} />
        </CardHeader>
        <CardContent className="space-y-2 text-xs text-muted-foreground">
          <p>Last sync: {connection?.lastSyncAt ? fullTimestamp(connection.lastSyncAt) : "never"}</p>
          {connection?.lastError && <p className="text-danger">{connection.lastError}</p>}
          {kind === "chat" && (granted.length > 0 || missing.length > 0) && <div className="space-y-1">
            {granted.length > 0 && <p>Granted: <span className="font-mono">{granted.join(", ")}</span></p>}
            {missing.length > 0 && <p className="text-warning">Missing: <span className="font-mono">{missing.join(", ")}</span></p>}
          </div>}
          {kind === "mail" && <div className="space-y-3 pt-1">
            <Button asChild size="sm"><a id="scout-connect-google" href="/api/google" aria-busy={connecting} aria-disabled={connecting}
              onClick={event => { if (connecting) { event.preventDefault(); return; } setConnecting(true); }}
              className={connecting ? "pointer-events-none opacity-80" : undefined}>
              {connecting && <LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />}{connecting ? "Opening Google…" : "Connect Google"}</a></Button>
            <details className="rounded-md border px-3 py-2">
              <summary className="cursor-pointer text-[13px] text-foreground">Google Workspace (admin)</summary>
              <ol className="mt-2 list-decimal space-y-1 pl-4">
                <li>In Google Cloud, create a service account for Scout and enable domain-wide delegation; copy its client ID.</li>
                <li>Enable the Gmail API in the same project.</li>
                <li>In the Admin console go to Security › Access and data control › API controls › Domain-wide delegation.</li>
                <li>Add the client ID with the scope <span className="font-mono">https://www.googleapis.com/auth/gmail.readonly</span> (plus <span className="font-mono">gmail.insert</span> for demo seeding).</li>
                <li>Give Scout the service-account key as a Secret and set the mailbox to impersonate; Scout only reads the authorized label.</li>
              </ol>
            </details>
          </div>}
        </CardContent>
      </Card>;
      return id === "invoicing" ? [card, <KsefCard key="ksef" connection={connections.find(entry => entry.id === "ksef")} />] : [card];
    })}</div>
  </section>;
}
