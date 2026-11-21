"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { tenantFetch } from "@/lib/tenant-browser";
import { moneyValue, type ForecastPlanResponse } from "@/lib/revenue-plan-model";

const eur = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
type Form = { target: string; noHiring: boolean; maxCost: string; guidance: string; enabled: boolean };
const EMPTY: Form = { target: "", noHiring: false, maxCost: "", guidance: "", enabled: true };

/** CEO settings of the Forecast loop: annual revenue target, plan constraints and guidance. Saving queues a review. */
export function ForecastSettingsPanel() {
  const router = useRouter();
  const [form, setForm] = useState<Form>(EMPTY);
  const [defaults, setDefaults] = useState<ForecastPlanResponse["defaults"] | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false), [loaded, setLoaded] = useState(false), [message, setMessage] = useState("");
  useEffect(() => {
    let active = true;
    tenantFetch("/api/forecast/plan").then(async response => {
      if (!response.ok) throw new Error(response.status === 403 ? "Only a workspace owner can set the revenue target and forecast plan." : "Forecast settings are temporarily unavailable. Reload to retry.");
      const value = await response.json() as ForecastPlanResponse;
      if (!active) return;
      setDefaults(value.defaults);
      setSaved(value.plan !== null);
      setForm(value.plan
        ? { target: value.plan.annual_revenue_target, noHiring: value.plan.no_hiring, maxCost: value.plan.max_new_monthly_cost ?? "", guidance: value.plan.guidance, enabled: value.plan.enabled }
        : { ...EMPTY, target: value.defaults.default_target });
      setLoaded(true);
    }).catch(error => { if (active) setMessage(error instanceof Error ? error.message : "Forecast settings are temporarily unavailable. Reload to retry."); });
    return () => { active = false; };
  }, []);

  async function save() {
    setBusy(true); setMessage("");
    try {
      const response = await tenantFetch("/api/forecast/plan", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        annual_revenue_target: form.target, no_hiring: form.noHiring, max_new_monthly_cost: form.maxCost.trim() || null, guidance: form.guidance, enabled: form.enabled,
      }) });
      const value = await response.json(); if (!response.ok) throw new Error(value.error);
      setSaved(true);
      setMessage(value.queued ? "Plan saved. Scout is reviewing the forecast against it; the result appears below." : "Plan saved.");
      router.refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not save the plan."); }
    finally { setBusy(false); }
  }
  async function run() {
    setBusy(true); setMessage("");
    try {
      const response = await tenantFetch("/api/forecast/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const value = await response.json(); if (!response.ok) throw new Error(value.error);
      setMessage(value.status === "already_scheduled" ? "A forecast review is already queued." : `Forecast review queued${value.task_id ? ` (${value.task_id})` : ""}. It appears below when done.`);
      router.refresh();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Could not run the forecast review."); }
    finally { setBusy(false); }
  }

  const defaultTarget = moneyValue(defaults?.default_target);
  const trailing = moneyValue(defaults?.trailing_revenue);
  const target = moneyValue(form.target.replace(/[\s€_]/g, "").replace(",", "."));
  const disabled = !loaded || busy;
  return <div id="forecast-plan-settings" className="space-y-3">
    <details id="forecast-plan-details" className="rounded-lg border bg-muted/20" open>
      <summary id="forecast-plan-summary" className="cursor-pointer px-3 py-2 text-sm font-medium">
        Revenue plan{loaded ? ` · ${target !== null ? eur.format(target) : "no target"} a year · ${saved ? form.enabled ? "Daily review on" : "Paused" : "Not saved yet"}` : ""}
      </summary>
      <div id="forecast-plan-fields" className="space-y-3 border-t p-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <label id="forecast-plan-target-label" htmlFor="forecast-plan-target" className="block text-xs font-medium">Annual revenue target (€)</label>
            <Input id="forecast-plan-target" inputMode="decimal" autoComplete="off" disabled={disabled} value={form.target} aria-describedby="forecast-plan-target-help"
              onChange={event => setForm({ ...form, target: event.target.value })} />
            <p id="forecast-plan-target-help" className="text-[11px] text-muted-foreground">
              Default: 150% of last 12 months{trailing !== null ? ` (${eur.format(trailing)})` : ""}{defaultTarget !== null ? ` = ${eur.format(defaultTarget)}` : ""}.
              {defaultTarget !== null && target !== defaultTarget && <> <button id="forecast-plan-use-default" type="button" className="underline underline-offset-2 hover:text-foreground" disabled={disabled}
                onClick={() => setForm({ ...form, target: defaults!.default_target })}>Use default</button></>}
            </p>
          </div>
          <div className="space-y-1">
            <label id="forecast-plan-max-cost-label" htmlFor="forecast-plan-max-cost" className="block text-xs font-medium">Max new monthly cost (€, optional)</label>
            <Input id="forecast-plan-max-cost" inputMode="decimal" autoComplete="off" placeholder="No limit" disabled={disabled} value={form.maxCost}
              onChange={event => setForm({ ...form, maxCost: event.target.value })} />
            <p className="text-[11px] text-muted-foreground">New recurring spend above this is flagged as drift.</p>
          </div>
        </div>
        <label id="forecast-plan-no-hiring-label" htmlFor="forecast-plan-no-hiring" className="flex min-h-11 items-center gap-2 text-sm">
          <input id="forecast-plan-no-hiring" type="checkbox" className="accent-primary" checked={form.noHiring} disabled={disabled} onChange={event => setForm({ ...form, noHiring: event.target.checked })} />
          No new hires — Scout alerts you and asks why if someone starts hiring
        </label>
        <label id="forecast-plan-guidance-label" htmlFor="forecast-plan-guidance" className="block text-xs font-medium">Guidance for the team</label>
        <Textarea id="forecast-plan-guidance" rows={3} maxLength={2000} disabled={disabled} value={form.guidance}
          placeholder="Priorities, e.g. grow catering orders, keep supplier costs flat, no new locations this year"
          onChange={event => setForm({ ...form, guidance: event.target.value })} />
        <label id="forecast-plan-enabled-label" htmlFor="forecast-plan-enabled" className="flex min-h-11 items-center gap-2 text-sm">
          <input id="forecast-plan-enabled" type="checkbox" className="accent-primary" checked={form.enabled} disabled={disabled} onChange={event => setForm({ ...form, enabled: event.target.checked })} />
          Run automatically every day
        </label>
        <Button id="forecast-plan-save" size="sm" disabled={disabled} onClick={save}>{busy ? "Working…" : "Save plan"}</Button>
      </div>
    </details>
    <div id="forecast-plan-run-row" className="flex flex-wrap items-center gap-3">
      <Button id="forecast-plan-run" size="sm" variant="outline" disabled={disabled} title={saved ? "Review with the last saved plan" : "Review against the default target"} onClick={run}>{busy ? "Working…" : "Run forecast now"}</Button>
      {message && <p id="forecast-plan-message" role="status" className="text-xs text-muted-foreground">{message}</p>}
    </div>
  </div>;
}
