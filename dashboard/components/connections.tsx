"use client";

import { useEffect, useState } from "react";
import { Building2, Database, HardDrive, LoaderCircle, Mail, MessagesSquare, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fullTimestamp, type Connection } from "@/lib/scout-model";

const CARDS: { kind: Connection["kind"]; title: string; description: string; icon: LucideIcon }[] = [
  { kind: "bank", title: "Revolut Business", description: "Bank transactions · mocked", icon: Building2 },
  { kind: "invoicing", title: "Invoicing", description: "Invoices and POs · Postgres", icon: Database },
  { kind: "mail", title: "Email", description: "Company mailbox · Gmail, read-only", icon: Mail },
  { kind: "chat", title: "Slack", description: "Chat context, DMs and attachments", icon: MessagesSquare },
  { kind: "files", title: "Company laptops", description: "Authorized work folders over VPN", icon: HardDrive },
];

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
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{CARDS.map(({ kind, title, description, icon: Icon }) => {
      const connection = connections.find(entry => entry.kind === kind);
      const status = !connection ? "not configured" : !connection.enabled ? "disabled" : connection.status;
      const granted = strings(connection?.config.scopes);
      const missing = strings(connection?.config.missing_scopes);
      return <Card key={kind} className="gap-3 shadow-none">
        <CardHeader className="flex flex-row items-start justify-between gap-3">
          <div className="flex items-center gap-3"><span className="flex size-8 items-center justify-center rounded-md border text-muted-foreground"><Icon className="size-4" aria-hidden="true" /></span>
            <div><CardTitle className="text-sm font-medium">{connection?.displayName && connection.displayName !== title ? `${title} · ${connection.displayName}` : title}</CardTitle><CardDescription className="text-xs">{description}</CardDescription></div></div>
          <Badge variant="outline" className={status === "connected" ? "border-success-border font-normal text-success" : status === "error" ? "border-danger-border font-normal text-danger" : "font-normal text-muted-foreground"}>
            <span className={`mr-1 inline-block size-1.5 rounded-full ${status === "connected" ? "bg-emerald-500" : status === "error" ? "bg-red-500" : "bg-muted-foreground/50"}`} aria-hidden="true" />{status}</Badge>
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
    })}</div>
  </section>;
}
