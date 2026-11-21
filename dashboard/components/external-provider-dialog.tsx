"use client";

import { tenantFetch } from "@/lib/tenant-browser";
import { useState } from "react";
import { EXTERNAL_PROVIDERS, parseExternalConfig, type ExternalConfig } from "@/lib/external-model";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function ExternalProviderDialog({ config, onClose, onSave, onRemove, persistForStrategy = false }: {
  persistForStrategy?: boolean; config: ExternalConfig | null; onClose: () => void; onSave: (value: ExternalConfig) => void; onRemove: () => void;
}) {
  const [provider, setProvider] = useState<ExternalConfig["provider"]>(config?.provider ?? EXTERNAL_PROVIDERS[0].id);
  const [model, setModel] = useState(config?.model ?? EXTERNAL_PROVIDERS[0].model);
  const [apiKey, setApiKey] = useState(config?.apiKey ?? "");
  const [persist, setPersist] = useState(persistForStrategy);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent id="scout-external-dialog" className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
      <DialogHeader id="scout-external-heading">
        <DialogTitle id="scout-external-title">External LLM provider</DialogTitle>
        <DialogDescription id="scout-external-description">Use your own API key to research hiring markets and ask Granite to run private forecasts.</DialogDescription>
      </DialogHeader>
      <form id="scout-external-form" className="space-y-4" onSubmit={async event => {
        event.preventDefault();
        setSaving(true); setError("");
        try {
          const config = parseExternalConfig({ provider, model, apiKey });
          if (persist) {
            const response = await tenantFetch("/api/strategy/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ external: config }) });
            const body = await response.json(); if (!response.ok) throw new Error(body.error || "Could not save strategy provider.");
          }
          onSave(config);
        }
        catch (failure) { setError((failure as Error).message); }
        finally { setSaving(false); }
      }}>
        <div id="scout-external-provider-field" className="space-y-1.5">
          <label id="scout-external-provider-label" htmlFor="scout-external-provider" className="text-sm font-medium">Provider</label>
          <select id="scout-external-provider" value={provider} className="h-11 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring" onChange={event => {
            const next = EXTERNAL_PROVIDERS.find(item => item.id === event.target.value)!;
            setProvider(next.id); setModel(next.model); setApiKey(""); setError("");
          }}>{EXTERNAL_PROVIDERS.map(item => <option id={`scout-external-provider-${item.id}`} key={item.id} value={item.id}>{item.name}</option>)}</select>
        </div>
        <div id="scout-external-model-field" className="space-y-1.5">
          <label id="scout-external-model-label" htmlFor="scout-external-model" className="text-sm font-medium">Model ID</label>
          <Input id="scout-external-model" className="h-11" value={model} maxLength={150} required aria-describedby="scout-external-model-help scout-external-error" onChange={event => { setModel(event.target.value); setError(""); }} />
          <p id="scout-external-model-help" className="text-xs text-muted-foreground">Choose a model available to your account that supports tool calls. Public hiring research is available with OpenAI and Mistral.</p>
        </div>
        <div id="scout-external-key-field" className="space-y-1.5">
          <label id="scout-external-key-label" htmlFor="scout-external-key" className="text-sm font-medium">API key</label>
          <Input id="scout-external-key" className="h-11" type="password" autoComplete="off" spellCheck={false} value={apiKey} maxLength={512} required aria-describedby="scout-external-key-help scout-external-error" onChange={event => { setApiKey(event.target.value); setError(""); }} />
          <p id="scout-external-key-help" className="text-xs text-muted-foreground">Kept in this tab unless you save it for Strategy below. Saved Strategy keys are encrypted on the server and survive reloads. Removing the chat provider only clears this tab.</p>
        </div>
        <p id="scout-external-privacy" className="rounded-md border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">Your public conversation and cited research are shared with the provider so follow-up messages keep their context. Do not paste private company data. Invoices and Granite's results stay private. Hiring proposals create simulated Liquidity actions on premise, without changing company records or hiring anyone.</p>
        <label id="scout-external-persist-label" htmlFor="scout-external-persist" className="flex min-h-11 items-center gap-2 text-sm"><input id="scout-external-persist" type="checkbox" className="accent-primary" checked={persist} disabled={persistForStrategy || saving} onChange={event => setPersist(event.target.checked)} />Save provider for the daily Strategy loop</label>
        {error && <p id="scout-external-error" role="alert" className="text-sm text-danger">{error}</p>}
        <DialogFooter id="scout-external-footer" className="gap-2">
          {config && <Button id="scout-external-remove" type="button" variant="ghost" className="h-11 sm:mr-auto" onClick={onRemove}>Remove provider</Button>}
          <Button id="scout-external-cancel" type="button" variant="outline" className="h-11" onClick={onClose}>Cancel</Button>
          <Button id="scout-external-save" type="submit" disabled={saving} className="h-11">{saving ? "Saving…" : persistForStrategy ? "Save provider" : "Use provider"}</Button>
        </DialogFooter>
      </form>
    </DialogContent>
  </Dialog>;
}
