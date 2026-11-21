"use client";

import { useEffect, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";
import { Activity, Coins, Gauge, MessagesSquare, RefreshCw } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { fillBuckets, formatUsageCost, type LlmUsageData } from "@/lib/llm-usage-model";
import { cn } from "@/lib/utils";

const RANGES = [1, 7, 30] as const;
type Range = (typeof RANGES)[number];
const number = (value: number | null) => value === null ? "—" : value.toLocaleString("en-US");
const compactNumber = (value: number) => value >= 1000 ? `${(value / 1000).toFixed(value >= 10000 ? 0 : 1)}k` : String(value);

// Use the same teal data accent and neutral comparison as the overview.
const requestsConfig = {
  ok: { label: "Succeeded", color: "var(--chart-balance)" },
  failed: { label: "Failed", color: "var(--danger)" },
} satisfies ChartConfig;
const tokensConfig = {
  prompt: { label: "Prompt tokens", color: "var(--chart-balance)" },
  completion: { label: "Completion tokens", color: "var(--chart-forecast)" },
} satisfies ChartConfig;
const latencyConfig = {
  p50: { label: "p50", color: "var(--chart-balance)" },
  p95: { label: "p95", color: "var(--chart-forecast)" },
} satisfies ChartConfig;
const actionConfig = { cost: { label: "Cost per action", color: "var(--chart-forecast)" } } satisfies ChartConfig;

// Responses per range survive view switches, so coming back to LLM usage shows numbers at once and refreshes quietly.
const cache = new Map<Range, LlmUsageData>();
const inflight = new Map<Range, Promise<LlmUsageData>>();
function fetchUsage(days: Range, signal?: AbortSignal): Promise<LlmUsageData> {
  const running = inflight.get(days);
  if (running && !signal) return running;
  const request = fetch(`/api/llm-usage?days=${days}`, { signal, cache: "no-store" })
    .then(response => response.json() as Promise<LlmUsageData>)
    .then(body => { cache.set(days, body); return body; })
    .finally(() => { if (inflight.get(days) === request) inflight.delete(days); });
  if (!signal) inflight.set(days, request);
  return request;
}

/** Warm the default range while the browser is idle, so the view opens with numbers. */
export function prefetchLlmUsage(days: Range) {
  if (!cache.has(days) && !inflight.has(days)) fetchUsage(days).catch(() => {});
}

/** Model telemetry from GET /api/llm-usage (the CFO API's /v1/llm/usage endpoints, read server-side). */
export function LlmUsage({ active }: { active: boolean }) {
  const [days, setDays] = useState<Range>(7);
  const [data, setData] = useState<LlmUsageData | null>(() => cache.get(7) ?? null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setFailed(false); setLoading(true);
    setData(cache.get(days) ?? null);
    (inflight.get(days) ?? fetchUsage(days, controller.signal)).then(body => { if (!controller.signal.aborted) setData(body); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [active, days, reload]);
  const report = data?.report;
  const snapshot = data?.snapshot;
  const buckets = report ? fillBuckets(report, days === 1 ? "hour" : "day") : [];
  const series = buckets.map(bucket => ({
    label: bucket.label, ok: bucket.requests - bucket.failedRequests, failed: bucket.failedRequests,
    prompt: bucket.promptTokens, completion: bucket.completionTokens, p50: bucket.p50Ms, p95: bucket.p95Ms,
  }));
  const actions = (report?.actions ?? []).map(row => ({ ...row, cost: Number(row.amortizedCost) })).sort((a, b) => b.cost - a.cost);
  const tokenTotal = report ? report.totals.promptTokens + report.totals.completionTokens : 0;
  const cacheShare = snapshot?.cacheUsage === null || snapshot?.cacheUsage === undefined ? null : Math.round(Number(snapshot.cacheUsage) * 100);

  return <section id="scout-llm-usage" className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-2xl font-medium tracking-tight">LLM usage</h1>
        <p className="mt-1 text-sm text-muted-foreground">Requests, tokens, latency and estimated cost of the self-hosted model.{data?.preview && " Sample data."}</p></div>
      <div className="flex items-center gap-2">
        <Button id="scout-llm-usage-reload" variant="ghost" size="icon" className="size-8" aria-label="Reload usage" onClick={() => setReload(value => value + 1)}><RefreshCw className={cn("size-4", loading && "motion-safe:animate-spin")} aria-hidden="true" /></Button>
        <div className="flex rounded-lg border bg-muted/40 p-0.5" role="group" aria-label="Range">{RANGES.map(range =>
          <button key={range} type="button" id={`scout-llm-usage-range-${range}`} aria-pressed={range === days} onClick={() => setDays(range)}
            className={cn("h-7 rounded-md px-3 text-[12px] text-muted-foreground hover:text-foreground", range === days && "bg-background font-medium text-foreground shadow-xs")}>{range === 1 ? "24 h" : `${range} days`}</button>)}</div>
      </div>
    </div>
    {failed && <p role="alert" className="text-sm text-danger">Usage could not be loaded.</p>}
    {!data && !failed ? <UsageSkeleton /> : data && <>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[
          { id: "requests", label: "Requests", value: report ? number(report.totals.requests) : "—", note: report ? `${report.totals.failedRequests} failed · ${report.totals.estimatedRequests} estimated` : "Report unavailable", icon: MessagesSquare },
          { id: "tokens", label: "Tokens", value: report ? compactNumber(tokenTotal) : "—", note: report ? `${number(report.totals.promptTokens)} in · ${number(report.totals.completionTokens)} out` : "", icon: Activity, share: report && tokenTotal ? report.totals.promptTokens / tokenTotal : null },
          { id: "latency", label: "Latency p50 / p95", value: report ? `${compactMs(report.totals.p50Ms)} / ${compactMs(report.totals.p95Ms)}` : "—", note: "Per request, end to end", icon: Gauge },
          { id: "cost", label: "Cost (amortized)", value: report ? formatUsageCost(report.totals.amortizedCost).replace(/(\.\d{2})\d+$/, "$1") : "—", note: report ? `busy time ${formatUsageCost(report.totals.busyCost)}` : "", icon: Coins },
        ].map(({ id, label, value, note, icon: Icon, share }) => <Card id={`scout-llm-metric-${id}`} key={id} className="min-w-0 gap-2 py-4 shadow-none"><CardContent className="space-y-2 px-4">
          <div className="flex items-center justify-between gap-2"><p className="text-xs text-muted-foreground">{label}</p>
            <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /></div>
          <strong className="block font-mono text-2xl font-medium tracking-tight">{value}</strong>
          {typeof share === "number" ? <div className="space-y-1"><div className="flex h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden="true"><span className="bg-[var(--chart-balance)]" style={{ width: `${share * 100}%` }} /><span className="flex-1 bg-[var(--chart-forecast)]" /></div><p className="text-[11px] text-muted-foreground">{note}</p></div>
            : <p className="text-[11px] text-muted-foreground">{note}</p>}
        </CardContent></Card>)}
      </div>

      {report && <div className="grid gap-4 xl:grid-cols-2">
        <ChartCard id="scout-llm-requests" title="Requests" description={days === 1 ? "Per hour, last 24 hours" : `Per day, last ${days} days`}>
          <ChartContainer config={requestsConfig} className="aspect-auto h-[220px] w-full">
            <BarChart data={series} margin={{ top: 8, right: 8, left: -12, bottom: 0 }} accessibilityLayer>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)" }} minTickGap={16} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)" }} width={40} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <ChartLegend content={<ChartLegendContent />} />
              <Bar dataKey="ok" stackId="r" fill="var(--color-ok)" radius={[0, 0, 0, 0]} isAnimationActive={false} />
              <Bar dataKey="failed" stackId="r" fill="var(--color-failed)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartContainer>
        </ChartCard>
        <ChartCard id="scout-llm-tokens" title="Tokens" description="Prompt and completion tokens">
          <ChartContainer config={tokensConfig} className="aspect-auto h-[220px] w-full">
            <BarChart data={series} margin={{ top: 8, right: 8, left: -4, bottom: 0 }} accessibilityLayer>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)" }} minTickGap={16} />
              <YAxis tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)" }} width={44} tickFormatter={value => compactNumber(Number(value))} />
              <ChartTooltip content={<ChartTooltipContent formatter={(value, name) => `${tokensConfig[name as keyof typeof tokensConfig]?.label ?? name}: ${Number(value).toLocaleString("en-US")}`} />} />
              <ChartLegend content={<ChartLegendContent />} />
              <Bar dataKey="prompt" stackId="t" fill="var(--color-prompt)" isAnimationActive={false} />
              <Bar dataKey="completion" stackId="t" fill="var(--color-completion)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartContainer>
        </ChartCard>
        <ChartCard id="scout-llm-latency" title="Latency" description="p50 and p95 per bucket, milliseconds">
          <ChartContainer config={latencyConfig} className="aspect-auto h-[220px] w-full">
            <LineChart data={series} margin={{ top: 8, right: 8, left: -4, bottom: 0 }} accessibilityLayer>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)" }} minTickGap={16} />
              <YAxis tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)" }} width={44} tickFormatter={value => compactMs(Number(value))} />
              <ChartTooltip content={<ChartTooltipContent formatter={(value, name) => `${name}: ${compactMs(Number(value))}`} />} />
              <ChartLegend content={<ChartLegendContent />} />
              <Line dataKey="p50" type="monotone" stroke="var(--color-p50)" strokeWidth={2} dot={{ r: 3 }} connectNulls isAnimationActive={false} />
              <Line dataKey="p95" type="monotone" stroke="var(--color-p95)" strokeWidth={2} strokeDasharray="5 4" dot={{ r: 3 }} connectNulls isAnimationActive={false} />
            </LineChart>
          </ChartContainer>
        </ChartCard>
        <ChartCard id="scout-llm-actions" title="Cost per action" description="Amortized cost of one Scout action, by kind">
          {actions.length ? <ChartContainer config={actionConfig} className="aspect-auto w-full" style={{ height: Math.max(140, actions.length * 34 + 30) }}>
            <BarChart data={actions} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }} accessibilityLayer>
              <CartesianGrid horizontal={false} />
              <XAxis type="number" tickLine={false} axisLine={false} tick={{ fill: "var(--muted-foreground)" }} tickFormatter={value => `€${Number(value).toFixed(2)}`} />
              <YAxis type="category" dataKey="action" tickLine={false} axisLine={false} width={130} tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} />
              <ChartTooltip content={<ChartTooltipContent hideLabel formatter={(_value, _name, item) => `${item.payload.action}: ${formatUsageCost(item.payload.amortizedCost)} · ${item.payload.actions} actions · ${item.payload.requests} requests`} />} />
              <Bar dataKey="cost" fill="var(--color-cost)" radius={[0, 4, 4, 0]} isAnimationActive={false} />
            </BarChart>
          </ChartContainer> : <p className="flex h-[180px] items-center justify-center text-sm text-muted-foreground">No attributed actions in this range.</p>}
        </ChartCard>
      </div>}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 rounded-xl border bg-card">
          <h2 className="border-b px-4 py-2.5 text-[13px] font-medium">Per action</h2>
          {actions.length ? <Table className="text-[13px]"><TableHeader><TableRow><TableHead>Action</TableHead><TableHead className="text-right">Actions</TableHead><TableHead className="text-right">Requests</TableHead><TableHead className="text-right">Cost / action</TableHead></TableRow></TableHeader>
            <TableBody>{actions.map(row => <TableRow key={row.action}><TableCell className="font-mono text-xs">{row.action}</TableCell><TableCell className="text-right">{row.actions}</TableCell><TableCell className="text-right">{row.requests}</TableCell><TableCell className="text-right font-mono text-xs">{formatUsageCost(row.amortizedCost)}</TableCell></TableRow>)}</TableBody></Table>
            : <p className="px-4 py-6 text-sm text-muted-foreground">No attributed actions in this range.</p>}
        </div>
        <Card className="h-fit gap-3 shadow-none"><CardHeader><CardTitle className="flex items-center gap-2 text-sm font-medium">
            <span className={cn("size-2 rounded-full", snapshot && !snapshot.stale ? "bg-primary motion-safe:animate-pulse" : "bg-muted-foreground")} aria-hidden="true" />Live model</CardTitle>
          <CardDescription>{snapshot ? snapshot.model : "Snapshot unavailable"}</CardDescription></CardHeader>
          {snapshot && <CardContent className="space-y-3 text-[13px]">
            {snapshot.stale && <Badge variant="outline" className="border-warning-border font-normal text-warning">stale</Badge>}
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border px-3 py-2"><p className="text-[11px] text-muted-foreground">Running</p><p className="font-mono text-lg">{number(snapshot.running)}</p></div>
              <div className="rounded-lg border px-3 py-2"><p className="text-[11px] text-muted-foreground">Waiting</p><p className={cn("font-mono text-lg", (snapshot.waiting ?? 0) > 0 && "text-warning")}>{number(snapshot.waiting)}</p></div>
            </div>
            <div className="space-y-1"><div className="flex justify-between text-[11px] text-muted-foreground"><span>KV cache</span><span className="font-mono">{cacheShare === null ? "—" : `${cacheShare}%`}</span></div>
              <div className="h-2 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full", (cacheShare ?? 0) > 85 ? "bg-danger" : (cacheShare ?? 0) > 60 ? "bg-warning-mark" : "bg-[var(--chart-balance)]")} style={{ width: `${cacheShare ?? 0}%` }} /></div></div>
            <p className="text-muted-foreground">Prompt {snapshot.promptRate ?? "—"} tok/s · generation {snapshot.generationRate ?? "—"} tok/s</p>
          </CardContent>}
        </Card>
      </div>
    </>}
    {report?.notes.length ? <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{report.notes.map(note => <li key={note}>{note}</li>)}</ul> : null}
  </section>;
}

function compactMs(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

function ChartCard({ id, title, description, children }: { id: string; title: string; description: string; children: React.ReactNode }) {
  return <Card id={id} className="min-w-0 gap-3 py-4 shadow-none"><CardHeader className="px-4"><CardTitle className="text-sm font-medium">{title}</CardTitle><CardDescription className="text-xs">{description}</CardDescription></CardHeader>
    <CardContent className="px-3">{children}</CardContent></Card>;
}

export function UsageSkeleton() {
  return <div className="space-y-4" role="status" aria-label="Loading usage">
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map(card => <Skeleton key={card} className="h-[108px] rounded-xl" />)}</div>
    <div className="grid gap-4 xl:grid-cols-2">{[0, 1, 2, 3].map(card => <Skeleton key={card} className="h-[290px] rounded-xl" />)}</div>
  </div>;
}
