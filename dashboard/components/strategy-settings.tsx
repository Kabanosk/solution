"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ExternalProviderDialog } from "@/components/external-provider-dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { tenantFetch } from "@/lib/tenant-browser";
import { DEFAULT_STRATEGY_PROMPT, type StrategySettings } from "@/lib/strategy-model";

export function StrategySettingsPanel() {
  const router = useRouter();
  const [settings, setSettings] = useState<StrategySettings>({ provider: null, model: null, companyContext: "", systemPrompt: DEFAULT_STRATEGY_PROMPT, enabled: false, nextRunAt: null });
  const [dialog, setDialog] = useState(false), [busy, setBusy] = useState(false), [loaded, setLoaded] = useState(false), [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    tenantFetch("/api/strategy/settings").then(async response => {
      if (!response.ok) throw new Error(response.status === 403 ? "Only a workspace owner can configure daily research." : "Strategy settings are temporarily unavailable. Reload to retry.");
      const value = await response.json(); if (active) { setSettings(value); setLoaded(true); }
    }).catch(error => { if (active) setMessage(error instanceof Error ? error.message : "Strategy settings are temporarily unavailable. Reload to retry."); });
    return () => { active = false; };
  }, []);
  async function save() {
    setBusy(true); setMessage("");
    try {
      const response = await tenantFetch("/api/strategy/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
      const value = await response.json(); if (!response.ok) throw new Error(value.error);
      setSettings(value); setMessage("Strategy settings saved.");
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not save settings."); }
    finally { setBusy(false); }
  }
  async function run() {
    setBusy(true); setMessage("Researching current news. This can take up to two minutes…");
    try {
      const response = await tenantFetch("/api/strategy/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const value = await response.json(); if (!response.ok) throw new Error(value.error);
      setMessage(value.running ? "A review is already running." : "Review recorded. Open its task below for the result and activity log."); router.refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not run review."); }
    finally { setBusy(false); }
  }
  return <div id="strategy-settings" className="space-y-3">
    {dialog && <ExternalProviderDialog config={null} persistForStrategy onClose={() => setDialog(false)} onRemove={() => {}} onSave={config => {
      setSettings(previous => ({ ...previous, provider: config.provider, model: config.model })); setDialog(false); setMessage("Provider saved for Strategy. Add company context and enable daily research.");
    }} />}
    <details id="strategy-settings-details" className="rounded-lg border bg-muted/20" open>
      <summary id="strategy-settings-summary" className="cursor-pointer px-3 py-2 text-sm font-medium">Daily news settings{settings.provider ? ` · ${settings.provider} · ${settings.enabled ? "Enabled" : "Paused"}` : " · Set up provider"}</summary>
      <div id="strategy-settings-fields" className="space-y-3 border-t p-3">
        <div id="strategy-provider-row" className="flex flex-wrap items-center justify-between gap-2">
          <p id="strategy-provider-status" className="break-all text-xs text-muted-foreground">{settings.provider ? `${settings.provider} · ${settings.model}` : "Use an OpenAI or Mistral provider with live web-search access."}</p>
          <Button id="strategy-configure-provider" size="sm" variant="outline" disabled={!loaded || busy} onClick={() => setDialog(true)}>{settings.provider ? "Change provider" : "Register provider"}</Button>
        </div>
        <label id="strategy-context-label" htmlFor="strategy-context" className="block text-xs font-medium">Company context</label>
        <Textarea id="strategy-context" rows={3} maxLength={4000} disabled={!loaded || busy} value={settings.companyContext} placeholder="Company, industry, markets, location and topics to watch" aria-describedby="strategy-data-help" onChange={event => setSettings({ ...settings, companyContext: event.target.value })} />
        <label id="strategy-prompt-label" htmlFor="strategy-prompt" className="block text-xs font-medium">System prompt</label>
        <Textarea id="strategy-prompt" rows={4} maxLength={8000} disabled={!loaded || busy} value={settings.systemPrompt} onChange={event => setSettings({ ...settings, systemPrompt: event.target.value })} />
        <p id="strategy-data-help" className="text-xs leading-5 text-muted-foreground">This context and prompt are sent to your provider to search news from the last 24 hours. Include only information you want shared. Private Scout records stay on premise. Provider usage may incur charges.</p>
        <label id="strategy-enabled-label" htmlFor="strategy-enabled" className="flex min-h-11 items-center gap-2 text-sm"><input id="strategy-enabled" type="checkbox" className="accent-primary" checked={settings.enabled} disabled={!loaded || busy} onChange={event => setSettings({ ...settings, enabled: event.target.checked })} />Run automatically every 24 hours</label>
        {settings.enabled && settings.nextRunAt && <p id="strategy-next-run" className="text-xs text-muted-foreground">Next review: {new Date(settings.nextRunAt).toLocaleString()}</p>}
        <Button id="strategy-save" size="sm" disabled={!loaded || busy} onClick={save}>{busy ? "Working…" : "Save settings"}</Button>
      </div>
    </details>
    <div id="strategy-run-row" className="flex flex-wrap items-center gap-3">
      <Button id="strategy-run" size="sm" variant="outline" disabled={!loaded || busy || !settings.provider} title="Run with the last saved settings" onClick={run}>{busy ? "Working…" : "Run review now"}</Button>
      {message && <p id="strategy-message" role="status" className="text-xs text-muted-foreground">{message}</p>}
    </div>
  </div>;
}
