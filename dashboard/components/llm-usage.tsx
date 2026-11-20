"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatUsageCost, type LlmUsageData } from "@/lib/llm-usage-model";

const RANGES = [1, 7, 30] as const;
const number = (value: number | null) => value === null ? "—" : value.toLocaleString("en-US");

/** Model telemetry from GET /api/llm-usage (the CFO API's /v1/llm/usage endpoints, read server-side). */
export function LlmUsage({ active }: { active: boolean }) {
  const [days, setDays] = useState<(typeof RANGES)[number]>(7);
  const [data, setData] = useState<LlmUsageData | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    setFailed(false);
    fetch(`/api/llm-usage?days=${days}`, { signal: controller.signal, cache: "no-store" })
      .then(response => response.json()).then(body => setData(body as LlmUsageData))
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, [active, days]);
  const report = data?.report;
  const snapshot = data?.snapshot;

  return <section className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-2xl font-medium tracking-tight">LLM usage</h1>
        <p className="mt-1 text-sm text-muted-foreground">Requests, tokens, latency and estimated cost of the self-hosted model.{data?.preview && " Sample data."}</p></div>
      <div className="flex gap-1" role="group" aria-label="Range">{RANGES.map(range =>
        <Button key={range} size="sm" variant={range === days ? "default" : "outline"} aria-pressed={range === days} onClick={() => setDays(range)}>{range === 1 ? "24 h" : `${range} days`}</Button>)}</div>
    </div>
    {failed && <p role="alert" className="text-sm text-danger">Usage could not be loaded.</p>}
    {!data && !failed && <p role="status" className="text-sm text-muted-foreground">Loading…</p>}
    {data && <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {[
        ["Requests", report ? `${number(report.totals.requests)}` : "—", report ? `${report.totals.failedRequests} failed · ${report.totals.estimatedRequests} estimated` : "Report unavailable"],
        ["Tokens", report ? number(report.totals.promptTokens + report.totals.completionTokens) : "—", report ? `${number(report.totals.promptTokens)} in · ${number(report.totals.completionTokens)} out` : ""],
        ["Latency p50 / p95", report ? `${number(report.totals.p50Ms)} / ${number(report.totals.p95Ms)} ms` : "—", ""],
        ["Cost (amortized)", report ? formatUsageCost(report.totals.amortizedCost) : "—", report ? `busy time ${formatUsageCost(report.totals.busyCost)}` : ""],
      ].map(([label, value, note]) => <Card key={label} className="gap-2 py-4 shadow-none"><CardContent className="space-y-1 px-4">
        <p className="text-xs text-muted-foreground">{label}</p><strong className="block font-mono text-xl font-medium">{value}</strong><p className="text-[11px] text-muted-foreground">{note}</p>
      </CardContent></Card>)}
    </div>}
    {data && <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="rounded-xl border bg-card">
        <h2 className="border-b px-4 py-2.5 text-[13px] font-medium">Per action</h2>
        {report?.actions.length ? <Table className="text-[13px]"><TableHeader><TableRow><TableHead>Action</TableHead><TableHead className="text-right">Actions</TableHead><TableHead className="text-right">Requests</TableHead><TableHead className="text-right">Cost / action</TableHead></TableRow></TableHeader>
          <TableBody>{report.actions.map(row => <TableRow key={row.action}><TableCell className="font-mono text-xs">{row.action}</TableCell><TableCell className="text-right">{row.actions}</TableCell><TableCell className="text-right">{row.requests}</TableCell><TableCell className="text-right font-mono text-xs">{formatUsageCost(row.amortizedCost)}</TableCell></TableRow>)}</TableBody></Table>
          : <p className="px-4 py-6 text-sm text-muted-foreground">No attributed actions in this range.</p>}
      </div>
      <Card className="h-fit gap-3 shadow-none"><CardHeader><CardTitle className="text-sm font-medium">Live model</CardTitle><CardDescription>{snapshot ? snapshot.model : "Snapshot unavailable"}</CardDescription></CardHeader>
        {snapshot && <CardContent className="space-y-1.5 text-[13px]">
          {snapshot.stale && <Badge variant="outline" className="border-warning-border font-normal text-warning">stale</Badge>}
          <p>Running {number(snapshot.running)} · waiting {number(snapshot.waiting)}</p>
          <p className="text-muted-foreground">Prompt {snapshot.promptRate ?? "—"} tok/s · generation {snapshot.generationRate ?? "—"} tok/s</p>
          <p className="text-muted-foreground">KV cache {snapshot.cacheUsage === null ? "—" : `${Math.round(Number(snapshot.cacheUsage) * 100)}%`}</p>
        </CardContent>}
      </Card>
    </div>}
    {report?.notes.length ? <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">{report.notes.map(note => <li key={note}>{note}</li>)}</ul> : null}
  </section>;
}
