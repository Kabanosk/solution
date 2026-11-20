"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { formatUsageCost, type LlmUsageData, type UsageReport } from "@/lib/llm-usage-model";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const number = (value: number) => value.toLocaleString("en-US");
const latency = (value: number | null) => value === null ? "Not recorded" : `${(value / 1000).toFixed(2)} s`;
const stamp = (value: string) => new Date(value).toISOString().slice(0, 19).replace("T", " ") + " UTC";

export function LlmUsage() {
  const [days, setDays] = useState(7);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<LlmUsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false); setData(null);
    fetch(`/api/llm-usage?days=${days}`, { cache: "no-store", signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("Usage unavailable");
        const body = await response.json() as LlmUsageData;
        if (!controller.signal.aborted) setData(body);
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [days, revision]);

  return <section id="lantern-llm" className="min-w-0 space-y-6" aria-labelledby="lantern-llm-title" aria-busy={loading}>
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div className="space-y-1"><h1 id="lantern-llm-title" className="text-xl font-medium tracking-tight">LLM</h1><p className="text-sm text-muted-foreground">Model usage and estimated cost</p></div>
      <Button id="lantern-llm-refresh" variant="ghost" size="sm" className="min-h-11 text-muted-foreground" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw aria-hidden="true" className={`size-3.5 ${loading ? "motion-safe:animate-spin" : ""}`} />{loading ? "Loading…" : error ? "Retry" : "Refresh"}</Button>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-3">
      <div role="group" aria-label="Usage period" className="flex flex-wrap gap-1">{[[1, "24 hours"], [7, "7 days"], [30, "30 days"]].map(([value, label]) => <Button id={`lantern-llm-range-${value}`} key={value} variant={days === value ? "secondary" : "ghost"} size="sm" aria-pressed={days === value} className="min-h-11 px-3 font-normal" onClick={() => setDays(Number(value))}>{label}</Button>)}</div>
      {data && <p className="text-xs text-muted-foreground">{data.preview ? "Sample data · " : ""}Updated <time dateTime={data.fetchedAt}>{stamp(data.fetchedAt)}</time></p>}
    </div>
    {loading && <p role="status" className="py-10 text-sm text-muted-foreground">Loading model usage…</p>}
    {!loading && error && <div id="lantern-llm-unavailable" role="status" className="space-y-2 py-10"><p className="text-sm">Model usage is temporarily unavailable.</p><p className="text-xs text-muted-foreground">Try refreshing in a moment.</p></div>}
    {!loading && data && <>
      {data.report ? <UsageHistory report={data.report} /> : <p id="lantern-llm-history-unavailable" role="status" className="text-sm text-muted-foreground">Usage history is unavailable. Refresh to try again.</p>}
      <section id="lantern-llm-snapshot" className="space-y-4 border-t pt-5" aria-labelledby="lantern-llm-snapshot-title">
        <div className="flex flex-wrap items-baseline justify-between gap-2"><h2 id="lantern-llm-snapshot-title" className="text-sm font-medium">Model snapshot</h2>{data.snapshot && <p className="break-all text-xs text-muted-foreground">{data.snapshot.model}</p>}</div>
        {data.snapshot ? <>
          <p className={`text-xs ${data.snapshot.stale ? "text-warning" : "text-muted-foreground"}`}><span>{data.snapshot.stale ? "Stale snapshot · " : "Sampled "}</span><time dateTime={data.snapshot.sampledAt}>{stamp(data.snapshot.sampledAt)}</time></p>
          <dl className="grid grid-cols-2 gap-5 sm:grid-cols-3 xl:grid-cols-5">
            {[
              ["Running requests", data.snapshot.running === null ? "Not reported" : number(data.snapshot.running)],
              ["Waiting requests", data.snapshot.waiting === null ? "Not reported" : number(data.snapshot.waiting)],
              ["KV cache used", data.snapshot.cacheUsage === null ? "Not reported" : `${(Number(data.snapshot.cacheUsage) * 100).toFixed(1)}%`],
              ["Input tokens / s", data.snapshot.promptRate === null ? "Not reported" : Number(data.snapshot.promptRate).toFixed(1)],
              ["Output tokens / s", data.snapshot.generationRate === null ? "Not reported" : Number(data.snapshot.generationRate).toFixed(1)],
            ].map(([label, value]) => <div key={label} className="space-y-2"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-words font-mono text-sm">{value}</dd></div>)}
          </dl>
        </> : <p role="status" className="text-sm text-muted-foreground">No model snapshot is available. Recorded usage remains visible above.</p>}
      </section>
    </>}
  </section>;
}

function UsageHistory({ report }: { report: UsageReport }) {
  const total = report.totals;
  return <>
    <dl id="lantern-llm-summary" className="grid gap-5 border-b pb-6 sm:grid-cols-2 xl:grid-cols-4">
      {[
        ["Requests", number(total.requests), `${number(total.failedRequests)} failed · ${number(total.estimatedRequests)} estimated`],
        ["Tokens", number(total.promptTokens + total.completionTokens), `${number(total.promptTokens)} input · ${number(total.completionTokens)} output`],
        ["Busy-time estimate", formatUsageCost(total.busyCost), "Based on recorded request time"],
        ["Allocated estimate", formatUsageCost(total.amortizedCost), "Based on time window and token share"],
      ].map(([label, value, detail]) => <div key={label} className="min-w-0 space-y-2"><dt className="text-xs text-muted-foreground">{label}</dt><dd className="break-words font-mono text-xl font-medium tracking-tight">{value}</dd><dd className="text-xs leading-5 text-muted-foreground">{detail}</dd></div>)}
    </dl>
    {total.requests === 0 ? <p id="lantern-llm-empty" role="status" className="py-6 text-sm text-muted-foreground">No model calls recorded in this period.</p> : <>
      <section className="space-y-3" aria-labelledby="lantern-llm-history-title">
        <div className="flex flex-wrap items-baseline justify-between gap-3"><h2 id="lantern-llm-history-title" className="text-sm font-medium">Recorded usage</h2><p className="text-xs text-muted-foreground">Latency p50 {latency(total.p50Ms)} · p95 {latency(total.p95Ms)}</p></div>
        <Table id="lantern-llm-history" className="table-fixed text-xs"><caption className="sr-only">Recorded requests, tokens and busy-time cost per UTC period</caption><TableHeader><TableRow><TableHead scope="col">Period (UTC)</TableHead><TableHead scope="col" className="w-20 text-right">Requests</TableHead><TableHead scope="col" className="w-24 text-right">Tokens</TableHead><TableHead scope="col" className="hidden w-36 text-right md:table-cell">Busy-time cost</TableHead></TableRow></TableHeader><TableBody>{report.buckets.map(bucket => <TableRow key={bucket.key}><TableCell className="whitespace-normal break-words py-3 font-mono">{bucket.key}</TableCell><TableCell className="text-right font-mono">{number(bucket.requests)}</TableCell><TableCell className="text-right font-mono">{number(bucket.promptTokens + bucket.completionTokens)}</TableCell><TableCell className="hidden text-right font-mono md:table-cell">{formatUsageCost(bucket.busyCost)}</TableCell></TableRow>)}</TableBody></Table>
      </section>
      {report.actions.length > 0 && <section className="space-y-3 border-t pt-5" aria-labelledby="lantern-llm-actions-title"><h2 id="lantern-llm-actions-title" className="text-sm font-medium">Cost per agent action</h2><ul className="divide-y">{report.actions.map(action => <li key={action.action} className="flex flex-wrap justify-between gap-x-6 gap-y-2 py-3 text-sm"><div className="min-w-0"><p className="break-words capitalize">{action.action.replaceAll("_", " ")}</p><p className="mt-1 text-xs text-muted-foreground">{number(action.actions)} actions · {number(action.requests)} model calls</p></div><div className="text-xs leading-6 sm:text-right"><p><span className="font-mono">{formatUsageCost(action.busyCost)}</span><span className="text-muted-foreground"> busy-time / action</span></p><p><span className="font-mono">{formatUsageCost(action.amortizedCost)}</span><span className="text-muted-foreground"> allocated / action</span></p></div></li>)}</ul></section>}
      {report.comparisons.length > 0 && <section className="space-y-3 border-t pt-5" aria-labelledby="lantern-llm-hosted-title"><h2 id="lantern-llm-hosted-title" className="text-sm font-medium">Same tokens at hosted list prices</h2><ul className="divide-y">{report.comparisons.map(item => <li key={item.model} className="flex flex-wrap justify-between gap-3 py-3 text-sm"><span>{item.model}<span className="ml-2 text-xs text-muted-foreground">Price dated {item.asOf}</span></span><span className="font-mono text-xs">{formatUsageCost(item.cost)}</span></li>)}</ul></section>}
    </>}
    <section id="lantern-llm-assumptions" className="space-y-3 border-t pt-5" aria-labelledby="lantern-llm-assumptions-title">
      <h2 id="lantern-llm-assumptions-title" className="text-sm font-medium">Cost assumptions</h2>
      <p className="text-xs leading-6 text-muted-foreground">Hourly rate €{report.hourlyRate}. Attributed token share {(Number(report.tokenShare) * 100).toFixed(1)}% ({report.shareSource === "vllm_metrics" ? "measured server tokens" : "all traffic assumed"}). Estimates are not billed charges.</p>
      <p className="text-xs leading-6 text-muted-foreground">Busy-time includes overlapping requests, so it is an upper bound. Allocation includes idle time within the measured window. Missing model usage is not replaced with estimates unless the backend marks its token counts as estimated.</p>
      {report.notes.map((note, index) => <p key={index} className="text-xs leading-6 text-muted-foreground">{note}</p>)}
    </section>
  </>;
}
