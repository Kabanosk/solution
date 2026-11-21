"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { AlertCircle, Check, ChevronDown, CircleCheck, FileCheck2, Landmark, Laptop, LoaderCircle, Mail, MessagesSquare, ReceiptText, ShieldCheck, X, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { apiHost, invoiceTotal, ksefSummary, parseKsefConfig } from "@/lib/ksef";
import { fullTimestamp, money, type Connection } from "@/lib/scout-model";
import { cn } from "@/lib/utils";

const SOURCES: { id: string; kind: Connection["kind"]; title: string; description: string; icon: LucideIcon }[] = [
  { id: "bank", kind: "bank", title: "Revolut Business", description: "Bank transactions · simulated bank connection", icon: Landmark },
  { id: "invoicing", kind: "invoicing", title: "Invoicing", description: "Invoices and purchase orders · Postgres", icon: ReceiptText },
  { id: "mail", kind: "mail", title: "Email", description: "Company mailbox · scoped Gmail access", icon: Mail },
  { id: "slack", kind: "chat", title: "Slack", description: "Authorized channels, direct messages and attachments", icon: MessagesSquare },
  { id: "laptops", kind: "files", title: "Company laptops", description: "Authorized work folders over VPN", icon: Laptop },
];

const statusOf = (connection: Connection | undefined) => !connection ? "not configured" : !connection.enabled ? "disabled" : connection.status;
const strings = (value: unknown) => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

function ConnectionRow({ id, title, description, icon: Icon, connection, action, attention = false, children }: {
  id: string; title: string; description: string; icon: LucideIcon; connection?: Connection;
  action?: ReactNode; attention?: boolean; children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const status = statusOf(connection);
  const label = status.replaceAll("_", " ");
  useEffect(() => { if (attention) setExpanded(true); }, [attention]);

  return <article id={`connection-${id}`} data-connection-row aria-labelledby={`connection-${id}-title`} className="min-w-0">
    <div className="grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] items-start gap-x-3 gap-y-3 px-4 py-4 sm:grid-cols-[2rem_minmax(0,1fr)_auto] sm:items-center sm:px-5">
      <span data-connection-icon className="flex size-8 items-center justify-center rounded-lg border bg-background text-muted-foreground"><Icon className="size-4" aria-hidden="true" /></span>
      <div className="min-w-0 space-y-1">
        <h3 id={`connection-${id}-title`} className="text-[13px] leading-5 font-medium">{title}</h3>
        <p className="text-xs leading-5 text-muted-foreground">{description}</p>
        {connection?.displayName && !connection.displayName.startsWith(title) && <p className="text-xs text-muted-foreground">{connection.displayName}</p>}
        {connection?.lastError && <p className="text-xs leading-5 break-words text-danger">{connection.lastError}</p>}
      </div>
      <div className="col-start-2 flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 sm:col-start-auto sm:justify-end">
        <div className="min-w-0 space-y-1 sm:text-right">
          <span data-connection-status className={cn("inline-flex items-center gap-1.5 text-xs", status === "error" ? "text-danger" : "text-muted-foreground")}>
            {status === "error" ? <AlertCircle className="size-3.5" aria-hidden="true" /> : <span className={cn("size-1.5 rounded-full", status === "connected" ? "bg-primary" : "bg-muted-foreground/50")} aria-hidden="true" />}
            <span className="capitalize">{label}</span>
          </span>
          <p id={`connection-${id}-sync`} className="text-[11px] leading-4 text-muted-foreground">{connection?.lastSyncAt ? `Synced ${fullTimestamp(connection.lastSyncAt)}` : "Not synced yet"}</p>
        </div>
        {action}
        {children && <Button id={`connection-${id}-toggle`} variant="ghost" size="icon" className="ml-auto size-8 shrink-0 text-muted-foreground sm:ml-0"
          aria-label={`${expanded ? "Hide" : "Show"} ${title} details`} aria-expanded={expanded} aria-controls={`connection-${id}-details`} onClick={() => setExpanded(value => !value)}>
          <ChevronDown className={cn("size-4 transition-transform motion-reduce:transition-none", expanded && "rotate-180")} aria-hidden="true" />
        </Button>}
      </div>
    </div>
    {children && <div id={`connection-${id}-details`} hidden={!expanded} className="min-w-0 border-t bg-muted/20 px-4 py-4 text-xs leading-5 text-muted-foreground sm:px-5">
      {expanded && children}
    </div>}
  </article>;
}

const POLL_MS = 1500;
const MAX_ROWS = 8;

function KsefConnection({ connection }: { connection: Connection | undefined }) {
  const router = useRouter();
  const config = parseKsefConfig(connection?.config);
  const running = config.phase === "running";
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => router.refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [running, router]);
  const host = apiHost(config.api);
  const rows = config.invoices.slice(0, MAX_ROWS);
  return <ConnectionRow id="ksef" title="KSeF" description="National e-invoice system · test environment" icon={FileCheck2} connection={connection} attention={running}
    action={<Button id="ksef-connect" variant="outline" size="sm" className="h-8 shadow-none" disabled>Sync unavailable</Button>}>
    <div className="min-w-0 space-y-3">
      {(config.nip || host) && <p id="ksef-target" className="break-words">{config.nip && <>NIP <span className="font-mono">{config.nip}</span></>}{config.nip && host && " · "}{host && <span className="font-mono">{host}</span>}</p>}
      {config.steps.length > 0 && <ol id="ksef-steps" aria-label="KSeF sync steps" className="space-y-2">
        {config.steps.map((entry, index) => <li id={`ksef-step-${index}`} key={`${index}-${entry.at ?? ""}`} className="flex items-start gap-2">
          {index === config.steps.length - 1 && running ? <LoaderCircle className="mt-0.5 size-3.5 shrink-0 motion-safe:animate-spin" aria-label="In progress" />
            : entry.ok ? <Check className="mt-0.5 size-3.5 shrink-0 text-primary" aria-label="Done" /> : <X className="mt-0.5 size-3.5 shrink-0 text-danger" aria-label="Failed" />}
          <span className={entry.ok ? "text-foreground" : "text-danger"}>{entry.text}</span>
        </li>)}
      </ol>}
      {config.phase === "done" && <div className="min-w-0 space-y-3">
        <p id="ksef-summary" className="text-[13px] text-foreground">{ksefSummary(config, money)}</p>
        {rows.length > 0 && <div className="min-w-0 overflow-hidden rounded-lg border bg-card"><Table id="ksef-invoices" className="text-xs">
          <TableHeader><TableRow><TableHead>Invoice</TableHead><TableHead>Supplier</TableHead><TableHead className="text-right">Total</TableHead><TableHead>KSeF number</TableHead><TableHead>Matched</TableHead></TableRow></TableHeader>
          <TableBody>{rows.map(entry => <TableRow key={`${entry.number}-${entry.ksefNumber}`}>
            <TableCell className="font-mono">{entry.number}</TableCell>
            <TableCell>{entry.supplier || "—"}</TableCell>
            <TableCell className="text-right tabular-nums">{invoiceTotal(entry, money)}</TableCell>
            <TableCell className="max-w-40 truncate font-mono" title={entry.ksefNumber}>{entry.ksefNumber || "—"}</TableCell>
            <TableCell>{entry.linked ? "Yes" : "No"}</TableCell>
          </TableRow>)}</TableBody>
        </Table></div>}
        {config.invoices.length > rows.length && <p>{config.invoices.length - rows.length} more not shown.</p>}
      </div>}
    </div>
  </ConnectionRow>;
}

export function Connections({ connections }: { connections: Connection[] }) {
  const [toast, setToast] = useState<{ text: string; ok: boolean } | null>(null);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const google = params.get("google");
    if (!google) return;
    setToast({ ok: google === "connected", text: google === "connected" ? "Google connected. Scout can now read the authorized mail label." : "Google connection failed. Try again." });
    params.delete("google");
    window.history.replaceState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}${window.location.hash}`);
    const timer = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(timer);
  }, []);

  const sources = SOURCES.map(source => ({ ...source, connection: connections.find(entry => source.kind === "invoicing" ? entry.id === source.id : entry.kind === source.kind) }));
  const ksef = connections.find(entry => entry.id === "ksef");
  const connected = [...sources.map(source => source.connection), ksef].filter(connection => statusOf(connection) === "connected").length;

  return <section id="scout-connections" aria-labelledby="scout-connections-title" className="space-y-5">
    <div><h1 id="scout-connections-title" className="text-2xl font-medium tracking-tight">Connections</h1>
      <p className="mt-1 text-sm text-muted-foreground">Manage the sources Scout can access. Permissions stay scoped to your workspace.</p></div>
    {toast && <div id="scout-connections-notice" role={toast.ok ? "status" : "alert"} className={cn("flex items-start gap-2 rounded-lg border bg-card px-4 py-3 text-[13px]", !toast.ok && "border-danger-border text-danger")}>
      {toast.ok ? <CircleCheck className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" /> : <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}{toast.text}
    </div>}
    <section id="scout-connections-list" aria-labelledby="scout-integrations-title" className="min-w-0 overflow-hidden rounded-xl border bg-card">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3 sm:px-5">
        <h2 id="scout-integrations-title" className="text-[13px] font-medium">Integrations</h2>
        <p id="scout-integrations-count" className="text-xs text-muted-foreground">{connected} of {sources.length + 1} connected</p>
      </header>
      <div className="divide-y">{sources.flatMap(({ id, kind, title, description, icon, connection }) => {
        const granted = strings(connection?.config.scopes);
        const missing = strings(connection?.config.missing_scopes);
        const details = kind === "chat" && (granted.length > 0 || missing.length > 0);
        const row = <ConnectionRow key={id} id={id} title={title} description={description} icon={icon} connection={connection}
          action={kind === "mail" ? <Button id="scout-connect-google" disabled variant="outline" size="sm" className="h-8 shadow-none">Mailbox connections unavailable</Button> : undefined}>
          {kind === "mail" ? <details id="scout-google-admin" className="min-w-0">
            <summary className="cursor-pointer font-medium text-foreground">Google Workspace (admin)</summary>
            <ol className="mt-3 list-decimal space-y-2 pl-4 [overflow-wrap:anywhere]">
              <li>In Google Cloud, create a service account for Scout and enable domain-wide delegation; copy its client ID.</li>
              <li>Enable the Gmail API in the same project.</li>
              <li>In the Admin console go to Security › Access and data control › API controls › Domain-wide delegation.</li>
              <li>Add the client ID with the scope <span className="font-mono">https://www.googleapis.com/auth/gmail.readonly</span> (plus <span className="font-mono">gmail.insert</span> for demo seeding).</li>
              <li>Give Scout the service-account key as a Secret and set the mailbox to impersonate; Scout only reads the authorized label.</li>
            </ol>
          </details> : details ? <div id="scout-slack-permissions" className="space-y-2 [overflow-wrap:anywhere]">
            {granted.length > 0 && <p>Granted permissions: <span className="font-mono">{granted.join(", ")}</span></p>}
            {missing.length > 0 && <p className="flex items-start gap-2 text-warning"><AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /><span>Missing permissions: <span className="font-mono">{missing.join(", ")}</span></span></p>}
          </div> : undefined}
        </ConnectionRow>;
        return id === "invoicing" ? [row, <KsefConnection key="ksef" connection={ksef} />] : [row];
      })}</div>
    </section>
    <p id="scout-connections-permissions-note" className="flex items-start gap-2 text-xs leading-5 text-muted-foreground"><ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />Scout only reads authorized sources. Connection status and last sync are reported by the worker.</p>
  </section>;
}
