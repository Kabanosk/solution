"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, Check, CircleCheck, Copy, Download, FileCheck2, Landmark, LoaderCircle, Mail, MessagesSquare, Network, ReceiptText, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  STEPS, adjacentStep, buildNetworkAnswers, callbackStep, canEdit, completionAction, connectProvider, nextStepAction, resolveStep, stepProgress, createHandoff, disconnectProvider, fetchSetup, handoffFiles,
  OnboardingError, patchSetup, parseSlackChannelIds, saveSlackChannels, startOAuth, stepStatus, validNip, type NetworkDraft, type Provider,
} from "@/lib/onboarding-client";
import type { NetworkAnswers, Setup, SetupHandoff, SetupStep, SetupStepId, SetupUpdate } from "@/lib/onboarding-model";
import { tenantFetch } from "@/lib/tenant-browser";
import { cn } from "@/lib/utils";

const ICONS: Record<SetupStepId, LucideIcon> = { bank: Landmark, invoicing: ReceiptText, ksef: FileCheck2, email: Mail, slack: MessagesSquare, network: Network };
const TONE = { muted: "bg-muted-foreground/50", primary: "bg-primary", warning: "bg-warning-mark", danger: "bg-danger" } as const;
const humanize = (key: string) => key.replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase());
const reason = (error: unknown) => error instanceof Error ? error.message : "Something went wrong. Try again.";
const CALLBACK = /^(google|slack)-(connected|failed)$/;

type Notice = { ok: boolean; text: string };
type Run = (key: SetupStepId | "all", action: () => Promise<unknown>) => Promise<boolean>;

export function Onboarding({ role, onContinue, initialStep }: { role: string | undefined; onContinue: () => void; initialStep?: SetupStepId }) {
  const editable = canEdit(role);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [current, setCurrent] = useState<SetupStepId | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState<SetupStepId | "all" | null>(null);
  const [agentOpen, setAgentOpen] = useState(false);
  const setupRef = useRef<Setup | null>(null);
  const requestedRef = useRef<SetupStepId | undefined>(initialStep);
  const movedRef = useRef(false);
  setupRef.current = setup;

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const next = await fetchSetup(tenantFetch);
      setupRef.current = next; setSetup(next);
      setCurrent(existing => existing ?? resolveStep(next, requestedRef.current));
    } catch (error) { setLoadError(reason(error)); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  // The OAuth callback redirects here with ?setup=<provider>-connected|failed. It is read before the URL is cleaned so the
  // wizard lands on that provider's step; show the outcome once, the loaded state stays authoritative.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const value = params.get("setup") ?? "";
    const outcome = CALLBACK.exec(value);
    if (!outcome) return;
    const target = callbackStep(value);
    if (target) { requestedRef.current = target; if (setupRef.current) setCurrent(target); }
    const name = outcome[1] === "google" ? "Google" : "Slack";
    setNotice(outcome[2] === "connected" ? { ok: true, text: `${name} authorization completed. The status below is what Scout verified.` } : { ok: false, text: `${name} connection did not complete. Nothing was connected; you can try again.` });
    params.delete("setup");
    window.history.replaceState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}${window.location.hash}`);
  }, []);

  // A later route change (#setup?step=slack) moves the wizard; the first value is handled by load().
  useEffect(() => {
    if (!initialStep || initialStep === requestedRef.current) return;
    requestedRef.current = initialStep;
    if (setupRef.current?.steps.some(entry => entry.id === initialStep)) { movedRef.current = true; setCurrent(initialStep); }
  }, [initialStep]);

  /** Runs one server mutation. A failure is shown and never applied locally; a 409 reloads the latest state. */
  const run: Run = async (key, action) => {
    setBusy(key); setNotice(null);
    try {
      const next = await action();
      if (next && typeof next === "object") { setupRef.current = next as Setup; setSetup(next as Setup); }
      return true;
    } catch (error) {
      if (error instanceof OnboardingError && error.conflict) {
        await load();
        setNotice({ ok: false, text: "Setup was changed in another session. We loaded the latest version and your last change was not applied. Review it and try again." });
      } else setNotice({ ok: false, text: reason(error) });
      return false;
    } finally { setBusy(null); }
  };
  const patch = (update: Omit<SetupUpdate, "revision">) => patchSetup(tenantFetch, { revision: setupRef.current?.revision ?? 0, ...update });
  const leave = async (action: "skip" | "complete") => { if (await run("all", () => patch({ action }))) onContinue(); };

  const active = setup ? setup.steps.find(entry => entry.id === (current ?? resolveStep(setup, requestedRef.current))) ?? setup.steps[0] : null;
  const progress = active ? stepProgress(active.id) : null;
  const previous = active ? adjacentStep(active.id, -1) : null;
  const following = active ? adjacentStep(active.id, 1) : null;
  const go = (id: SetupStepId) => { movedRef.current = true; setNotice(null); setCurrent(id); };
  /** Proceeds past the current step: to the next one, or, after the last, saves completion (or dismissal) before leaving. */
  const proceed = async () => {
    if (following) go(following);
    else if (setupRef.current) await leave(completionAction(setupRef.current));
  };
  const next = async () => {
    if (!active) return;
    if (!editable) { if (following) go(following); else onContinue(); return; }
    // Unresolved optional steps are skipped explicitly (saved first) so Next never leaves them looking untouched.
    if (nextStepAction(active) === "skip" && !(await run(active.id, () => patch({ action: "step", step: active.id, state: "skipped" })))) return;
    await proceed();
  };
  const skipStep = async () => { if (active && await run(active.id, () => patch({ action: "step", step: active.id, state: "skipped" }))) await proceed(); };
  const resolving = active !== null && editable && nextStepAction(active) === "skip";

  return <section id="scout-onboarding" aria-labelledby="scout-onboarding-title" className="mx-auto w-full max-w-2xl space-y-5 py-2 md:py-6">
    <div>
      <h1 id="scout-onboarding-title" className="text-2xl font-medium tracking-tight">Your workspace is ready</h1>
      <p className="mt-1 text-sm text-muted-foreground">Connect what Scout should work with, one step at a time. Everything is optional.</p>
    </div>
    {!editable && <p id="scout-onboarding-readonly" role="note" className="rounded-lg border bg-muted/40 px-4 py-3 text-[13px]">Only workspace owners can change setup. You can see its progress here; ask an owner to connect or skip a step.</p>}
    {notice && <div id="scout-onboarding-notice" role={notice.ok ? "status" : "alert"} className={cn("flex items-start gap-2 rounded-lg border bg-card px-4 py-3 text-[13px]", !notice.ok && "border-danger-border text-danger")}>
      {notice.ok ? <CircleCheck className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" /> : <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}<span className="min-w-0 break-words">{notice.text}</span>
    </div>}

    {!setup && !loadError && <div id="scout-onboarding-loading" role="status" aria-label="Loading setup" className="overflow-hidden rounded-xl border bg-card">
      <div className="flex items-center gap-3 px-4 py-4"><Skeleton className="size-8 rounded-lg" /><div className="flex-1 space-y-2"><Skeleton className="h-3.5 w-24" /><Skeleton className="h-3 w-3/4" /></div></div>
    </div>}
    {loadError && !setup && <div id="scout-onboarding-error" role="alert" className="rounded-xl border border-danger-border bg-card px-4 py-6 text-center text-sm">
      <p className="text-danger">{loadError}</p>
      <Button id="scout-onboarding-retry" variant="outline" size="sm" className="mt-3 h-8" onClick={() => void load()}>Try again</Button>
    </div>}

    {setup && active && progress && <>
      <div id="scout-onboarding-progress" className="space-y-2">
        <div className="flex items-baseline justify-between gap-3 text-xs">
          <span id="scout-onboarding-progress-label" className="font-medium">{progress.label}</span>
          <span className="min-w-0 truncate text-muted-foreground">{STEPS.find(entry => entry.id === active.id)?.title}</span>
        </div>
        <div id="scout-onboarding-progressbar" role="progressbar" aria-label="Setup progress" aria-valuemin={1} aria-valuemax={progress.total} aria-valuenow={progress.number} aria-valuetext={`${progress.label}: ${STEPS.find(entry => entry.id === active.id)?.title}`} className="flex gap-1">
          {STEPS.map((entry, index) => <span key={entry.id} className={cn("h-1 flex-1 rounded-full", index < progress.number ? "bg-primary" : "bg-muted")} />)}
        </div>
      </div>
      <div id="scout-onboarding-steps" className="overflow-hidden rounded-xl border bg-card">
        <StepRow key={active.id} step={active} network={setup.network} editable={editable} busy={busy} run={run} patch={patch} reload={load} onSkip={() => void skipStep()} focusHeading={movedRef.current} />
      </div>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button id="scout-onboarding-back" variant="outline" className="h-10" disabled={busy !== null || previous === null} onClick={() => previous && go(previous)}>Back</Button>
          <Button id="scout-onboarding-next" variant={resolving ? "outline" : "default"} className="h-10" disabled={busy !== null} onClick={() => void next()}>
            {busy === "all" && <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" />}{following ? "Next" : "Finish setup"}</Button>
          {editable && <Button id="scout-onboarding-agent" variant="outline" className="h-10 sm:ml-auto" disabled={busy !== null} onClick={() => setAgentOpen(true)}>Continue with your agent</Button>}
        </div>
        {resolving && <p id="scout-onboarding-next-hint" className="text-xs leading-5 text-muted-foreground">{following ? "Next skips this step." : "Finishing skips this step."} You can set it up later from Connections.</p>}
        <p className="text-xs leading-5 text-muted-foreground">
          {editable && <><Button id="scout-onboarding-skip" variant="link" className="h-auto p-0 text-xs" disabled={busy !== null} onClick={() => void leave("skip")}>Skip setup for now</Button> · </>}
          You can finish setup from Connections.</p>
      </div>
    </>}
    {editable && <AgentDialog open={agentOpen} onOpenChange={setAgentOpen} />}
  </section>;
}

function StepRow({ step, network, editable, busy, run, patch, reload, onSkip, focusHeading }: {
  step: SetupStep; network: NetworkAnswers | null; editable: boolean; busy: SetupStepId | "all" | null; run: Run;
  patch: (update: Omit<SetupUpdate, "revision">) => Promise<Setup>; reload: () => Promise<void>; onSkip: () => void; focusHeading: boolean;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (focusHeading) heading.current?.focus(); }, [focusHeading]);
  const meta = STEPS.find(entry => entry.id === step.id)!;
  const Icon = ICONS[step.id];
  const status = stepStatus(step);
  const here = busy === step.id;
  const disabled = busy !== null;
  const [form, setForm] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [nip, setNip] = useState("");
  const [mailLabel, setMailLabel] = useState("INBOX");
  const [nipError, setNipError] = useState<string | null>(null);
  const settled = step.state === "connected" || step.state === "sandbox_connected";
  const slackAuthorized = step.id === "slack" && step.display.authorized === true;
  const facts = Object.entries(step.display).filter(([key, value]) => key !== "channel_ids" && value !== null && value !== "").slice(0, 6);
  const id = (suffix: string) => `scout-onboarding-${suffix}-${step.id}`;

  const connect = () => {
    if (step.id === "email" || step.id === "slack") {
      const provider = step.id === "email" ? "google" : "slack";
      return run(step.id, async () => { const result = await startOAuth(tenantFetch, provider, provider === "google" ? { label: mailLabel.trim() || "INBOX" } : {}); if ("url" in result) { window.location.assign(result.url); return null; } return result.setup; });
    }
    return run(step.id, () => connectProvider(tenantFetch, meta.provider as Exclude<Provider, "google" | "slack">, step.id === "ksef" ? { nip: nip.trim() } : step.id === "bank" && apiKey.trim() ? { api_key: apiKey.trim() } : {}));
  };
  const primary = () => { if (step.id === "ksef" || step.id === "network") setForm(open => !open); else void connect(); };
  const resume = () => run(step.id, () => patch({ action: "step", step: step.id, state: "not_started" }));

  return <div id={`scout-onboarding-step-${step.id}`} data-onboarding-step={step.id} data-state={step.state} role="group" aria-labelledby={id("title")} className="min-w-0">
    <div className="grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] items-start gap-x-3 gap-y-3 px-4 py-4 sm:px-5">
      <span className="flex size-8 items-center justify-center rounded-lg border bg-background text-muted-foreground"><Icon className="size-4" aria-hidden="true" /></span>
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          <h2 id={id("title")} ref={heading} tabIndex={-1} className="text-[13px] leading-5 font-medium outline-none">{meta.title}</h2>
          <span id={id("status")} data-onboarding-status={step.state} className={cn("inline-flex items-center gap-1.5 text-xs", status.tone === "danger" ? "text-danger" : status.tone === "warning" ? "text-warning" : "text-muted-foreground")}>
            {status.tone === "danger" ? <AlertCircle className="size-3.5" aria-hidden="true" /> : status.tone === "primary" ? <Check className="size-3.5 text-primary" aria-hidden="true" /> : <span className={cn("size-1.5 rounded-full", TONE[status.tone])} aria-hidden="true" />}
            {status.label}
          </span>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">{meta.description}</p>
        {step.message && <p id={id("message")} className={cn("text-xs leading-5 break-words", step.state === "failed" ? "text-danger" : "text-foreground")}>{step.message}</p>}
        {facts.length > 0 && <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 text-xs leading-5 text-muted-foreground">
          {facts.map(([key, value]) => <div key={key} className="contents"><dt>{humanize(key)}</dt><dd className="min-w-0 break-words font-mono text-foreground">{typeof value === "boolean" ? (value ? "Yes" : "No") : value}</dd></div>)}
        </dl>}

        {editable && <div className="flex flex-wrap items-center gap-2 pt-2">
          {step.state === "skipped" ? <Button id={id("resume")} variant="outline" size="sm" className="h-8" disabled={disabled} onClick={() => void resume()}>Set up</Button> : <>
            {step.state === "connecting" && <><Button id={id("refresh")} variant="outline" size="sm" className="h-8" disabled={disabled} onClick={() => void reload()}>Check status</Button>{meta.provider && <Button id={id("cancel")} variant="ghost" size="sm" className="h-8" disabled={disabled} onClick={() => void run(step.id, () => disconnectProvider(tenantFetch, meta.provider!))}>Cancel authorization</Button>}</>}
            {(!settled && !slackAuthorized && step.state !== "connecting") || step.id === "network" ? <Button id={id("action")} variant={settled ? "outline" : "default"} size="sm" className="h-8" disabled={disabled} aria-expanded={step.id === "ksef" || step.id === "network" ? form : undefined} onClick={primary}>
              {here && <LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />}{step.state === "failed" ? "Try again" : step.id === "network" && step.state !== "not_started" ? "Change answers" : meta.cta}</Button> : null}
            {(settled || slackAuthorized) && meta.provider && <Button id={id("disconnect")} variant="outline" size="sm" className="h-8" disabled={disabled} onClick={() => void run(step.id, () => disconnectProvider(tenantFetch, meta.provider!))}>Disconnect</Button>}
            {step.state !== "connecting" && !settled && !slackAuthorized && <Button id={`scout-onboarding-skip-${step.id}`} variant="ghost" size="sm" className="h-8 text-muted-foreground" disabled={disabled} onClick={onSkip}>Skip<span className="sr-only"> {meta.title}</span></Button>}
          </>}
        </div>}

        {editable && step.id === "bank" && !settled && step.state !== "skipped" && <details className="pt-1 text-xs text-muted-foreground">
          <summary className="cursor-pointer">Use my own bunq sandbox API key</summary>
          <div className="mt-2 max-w-sm space-y-1.5">
            <label htmlFor="scout-onboarding-bunq-key" className="block font-medium text-foreground">bunq sandbox API key (optional)</label>
            <Input id="scout-onboarding-bunq-key" type="password" autoComplete="off" spellCheck={false} value={apiKey} onChange={event => setApiKey(event.target.value)} className="h-9" />
            <p>Leave empty to create a sandbox company with test data. Sandbox keys only.</p>
          </div>
        </details>}

        {editable && step.id === "email" && !settled && step.state !== "skipped" && step.state !== "connecting" && <details className="pt-1 text-xs text-muted-foreground">
          <summary className="cursor-pointer">Choose the mail label Scout may read</summary>
          <div className="mt-2 max-w-sm space-y-1.5"><label htmlFor="scout-onboarding-gmail-label" className="block font-medium text-foreground">Gmail label</label>
            <Input id="scout-onboarding-gmail-label" value={mailLabel} maxLength={100} onChange={event => setMailLabel(event.target.value)} className="h-9" />
            <p>Defaults to INBOX. Google grants read-only mailbox permission; Scout restricts its reads to this label.</p></div>
        </details>}
        {editable && slackAuthorized && <details id="scout-onboarding-slack-manual" className="pt-1 text-xs text-muted-foreground" open={!settled}>
          <summary className="cursor-pointer">Edit channels manually</summary>
          <SlackChannelsForm initial={typeof step.display.channel_ids === "string" ? step.display.channel_ids : ""} disabled={disabled} onSubmit={channels => run("slack", () => saveSlackChannels(tenantFetch, channels))} />
        </details>}

        {editable && form && step.id === "ksef" && <form id="scout-onboarding-ksef-form" className="max-w-sm space-y-1.5 pt-2" noValidate
          onSubmit={event => { event.preventDefault(); if (!validNip(nip.trim())) { setNipError("Enter the 10-digit NIP, digits only."); return; } setNipError(null); void connect(); }}>
          <label htmlFor="scout-onboarding-ksef-nip" className="block text-xs font-medium">Company NIP</label>
          <Input id="scout-onboarding-ksef-nip" inputMode="numeric" autoComplete="off" value={nip} onChange={event => setNip(event.target.value)} aria-invalid={nipError ? true : undefined} aria-describedby={nipError ? "scout-onboarding-ksef-nip-error" : undefined} className="h-9 font-mono" />
          {nipError && <p id="scout-onboarding-ksef-nip-error" role="alert" className="text-xs text-danger">{nipError}</p>}
          <p className="text-xs text-muted-foreground">Test environment only. Nothing is submitted to the tax authority.</p>
          <Button id="scout-onboarding-ksef-submit" type="submit" size="sm" className="h-8" disabled={disabled}>Check test environment</Button>
        </form>}

        {editable && form && step.id === "network" && <NetworkForm initial={network} disabled={disabled} onSubmit={answers => run("network", () => patch({ action: "network", network: answers })).then(ok => { if (ok) setForm(false); })} />}
      </div>
    </div>
  </div>;
}

function SlackChannelsForm({ initial, disabled, onSubmit }: { initial: string; disabled: boolean; onSubmit: (channels: string[]) => Promise<boolean> }) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  return <form id="scout-onboarding-slack-channels-form" className="max-w-md space-y-2 pt-2" onSubmit={event => {
    event.preventDefault();
    const channels = parseSlackChannelIds(value);
    if (channels === null) { setError("Enter at most 100 Slack channel IDs, starting with C or G, separated by commas or spaces."); return; }
    setError(null); void onSubmit(channels);
  }}>
    <label htmlFor="scout-onboarding-slack-channels" className="block text-xs font-medium">Slack channel IDs</label>
    <Input id="scout-onboarding-slack-channels" value={value} onChange={event => setValue(event.target.value)} placeholder="C0123456789, G0123456789" autoComplete="off" className="h-9 font-mono" aria-invalid={error ? true : undefined} aria-describedby="scout-onboarding-slack-channels-hint" />
    <p id="scout-onboarding-slack-channels-hint" className="text-xs leading-5 text-muted-foreground">Scout joined these channels when you connected Slack. Remove IDs to stop reading a channel, or add one (invite Scout’s bot to it first). An empty list turns channel reading off.</p>
    {error && <p id="scout-onboarding-slack-channels-error" role="alert" className="text-xs text-danger">{error}</p>}
    <Button id="scout-onboarding-slack-channels-save" type="submit" size="sm" className="h-8" disabled={disabled}>Save channels</Button>
  </form>;
}

function NetworkForm({ initial, disabled, onSubmit }: { initial: NetworkAnswers | null; disabled: boolean; onSubmit: (answers: NetworkAnswers) => void }) {
  const [draft, setDraft] = useState<NetworkDraft>({ required: initial?.required ?? false, provider: initial?.provider ?? "", protocol: initial?.protocol ?? "", administrator: initial?.administrator ?? "", systems: initial?.systems.join(", ") ?? "", companyOnly: initial?.company_only ?? false });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const set = <K extends keyof NetworkDraft>(key: K, value: NetworkDraft[K]) => setDraft(current => ({ ...current, [key]: value }));
  const field = (key: "provider" | "protocol" | "administrator", label: string, hint?: string) => <div className="space-y-1.5">
    <label htmlFor={`scout-onboarding-network-${key}`} className="block text-xs font-medium">{label}</label>
    <Input id={`scout-onboarding-network-${key}`} autoComplete="off" value={draft[key]} onChange={event => set(key, event.target.value)} aria-invalid={errors[key] ? true : undefined} aria-describedby={errors[key] ? `scout-onboarding-network-${key}-error` : undefined} className="h-9" placeholder={hint} />
    {errors[key] && <p id={`scout-onboarding-network-${key}-error`} role="alert" className="text-xs text-danger">{errors[key]}</p>}
  </div>;
  return <form id="scout-onboarding-network-form" noValidate className="max-w-md space-y-3 pt-2" onSubmit={event => {
    event.preventDefault();
    const result = buildNetworkAnswers(draft);
    if (result.ok) { setErrors({}); onSubmit(result.answers); } else setErrors(result.errors);
  }}>
    <fieldset className="space-y-1.5"><legend className="text-xs font-medium">Does Scout need access to systems on your company network?</legend>
      <div className="flex gap-4 text-xs">
        <label htmlFor="scout-onboarding-network-required-no" className="flex items-center gap-2"><input id="scout-onboarding-network-required-no" type="radio" name="network-required" className="size-4 accent-primary" checked={!draft.required} onChange={() => set("required", false)} />No</label>
        <label htmlFor="scout-onboarding-network-required-yes" className="flex items-center gap-2"><input id="scout-onboarding-network-required-yes" type="radio" name="network-required" className="size-4 accent-primary" checked={draft.required} onChange={() => set("required", true)} />Yes</label>
      </div>
    </fieldset>
    {draft.required && <div className="space-y-3">
      {field("provider", "VPN provider", "e.g. WireGuard, Tailscale")}
      {field("protocol", "Protocol")}
      {field("administrator", "Responsible administrator", "Name or team")}
      <div className="space-y-1.5">
        <label htmlFor="scout-onboarding-network-systems" className="block text-xs font-medium">Systems Scout needs to reach</label>
        <Textarea id="scout-onboarding-network-systems" rows={2} value={draft.systems} onChange={event => set("systems", event.target.value)} aria-invalid={errors.systems ? true : undefined} aria-describedby={errors.systems ? "scout-onboarding-network-systems-error" : undefined} placeholder="Separate with commas or new lines" />
        {errors.systems && <p id="scout-onboarding-network-systems-error" role="alert" className="text-xs text-danger">{errors.systems}</p>}
      </div>
      <div className="space-y-1.5">
        <label htmlFor="scout-onboarding-network-company-only" className="flex items-start gap-2 text-xs"><input id="scout-onboarding-network-company-only" type="checkbox" className="mt-0.5 size-4 accent-primary" checked={draft.companyOnly} onChange={event => set("companyOnly", event.target.checked)} aria-invalid={errors.companyOnly ? true : undefined} />I approve access to company folders and company mail only, never personal files.</label>
        {errors.companyOnly && <p id="scout-onboarding-network-company-only-error" role="alert" className="text-xs text-danger">{errors.companyOnly}</p>}
      </div>
      <p className="text-xs leading-5 text-muted-foreground">Do not enter private keys, passwords or VPN profiles. Your administrator sets up access; this stays marked as requiring administrator setup until then.</p>
    </div>}
    <Button id="scout-onboarding-network-submit" type="submit" size="sm" className="h-8" disabled={disabled}>Save answers</Button>
  </form>;
}

function AgentDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [handoff, setHandoff] = useState<SetupHandoff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState("");
  const [fallback, setFallback] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    let active = true;
    setHandoff(null); setError(null); setStatus(""); setFallback(null);
    createHandoff(tenantFetch).then(next => { if (active) setHandoff(next); }, (reasonValue: unknown) => { if (active) setError(reason(reasonValue)); });
    return () => { active = false; };
  }, [open, attempt]);

  const copy = async (text: string, what: string) => {
    try { await navigator.clipboard.writeText(text); setFallback(null); setStatus(`${what} copied.`); }
    catch { setFallback(text); setStatus(`Your browser blocked copying. Select the text below and copy it yourself.`); }
  };
  const download = (file: { name: string; mime: string; content: string }) => {
    const url = URL.createObjectURL(new Blob([file.content], { type: `${file.mime};charset=utf-8` }));
    const link = document.createElement("a"); link.href = url; link.download = file.name; document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus(`${file.name} downloaded.`);
  };
  const files = handoff ? handoffFiles(handoff) : [];

  return <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent id="scout-onboarding-agent-dialog" className="max-h-[90dvh] max-w-lg overflow-y-auto" onCloseAutoFocus={event => { event.preventDefault(); document.getElementById("scout-onboarding-agent")?.focus(); }}>
      <DialogHeader><DialogTitle>Continue with your agent</DialogTitle>
        <DialogDescription className="leading-6">Give these instructions to any agent that can follow written steps, such as Codex or Claude Code. It guides you through setup. You sign in and approve each provider yourself, and Scout gives the agent no credentials or access.</DialogDescription></DialogHeader>
      {!handoff && !error && <p id="scout-onboarding-agent-loading" role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" />Preparing instructions…</p>}
      {error && <div id="scout-onboarding-agent-error" role="alert" className="space-y-2 text-sm text-danger"><p>{error}</p><Button id="scout-onboarding-agent-retry" variant="outline" size="sm" onClick={() => setAttempt(value => value + 1)}>Try again</Button></div>}
      {handoff && <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button id="scout-onboarding-copy-link" variant="outline" size="sm" className="h-9 gap-2" onClick={() => void copy(handoff.setupUrl, "Setup link")}><Copy className="size-4" aria-hidden="true" />Copy setup link</Button>
          <Button id="scout-onboarding-copy-instructions" variant="outline" size="sm" className="h-9 gap-2" aria-label="Copy instructions" onClick={() => void copy(handoff.prompt, "Instructions")}><Copy className="size-4" aria-hidden="true" />Copy instructions</Button>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button id="scout-onboarding-download-md" variant="outline" size="sm" className="h-9 gap-2" onClick={() => download(files[0])}><Download className="size-4" aria-hidden="true" />Download Markdown</Button>
          <Button id="scout-onboarding-download-json" variant="outline" size="sm" className="h-9 gap-2" onClick={() => download(files[1])}><Download className="size-4" aria-hidden="true" />Download JSON</Button>
        </div>
        {handoff.claudeUrl && <p className="text-xs leading-5 text-muted-foreground">Optional: <a id="scout-onboarding-claude-link" href={handoff.claudeUrl} className="underline underline-offset-2">open in Claude Code</a>. Works only if Claude Code is installed; it does not send anything for you.</p>}
        <p id="scout-onboarding-agent-status" role="status" aria-live="polite" className="min-h-5 text-xs text-muted-foreground">{status}</p>
        {fallback !== null && <Textarea id="scout-onboarding-copy-fallback" aria-label="Text to copy manually" readOnly rows={6} value={fallback} className="font-mono text-xs" onFocus={event => event.currentTarget.select()} />}
        <p className="text-xs leading-5 text-muted-foreground">The link only opens your normal sign-in; sharing it grants no access.</p>
      </div>}
    </DialogContent>
  </Dialog>;
}
