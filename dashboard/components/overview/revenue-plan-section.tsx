"use client";

import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";
import { Bar, CartesianGrid, ComposedChart, Line, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Button } from "@/components/ui/button";
import type { AgentTask } from "@/lib/scout-model";
import { driftAlerts, gapShare, isShort, revenueChartRows, type RevenuePlan } from "@/lib/revenue-plan-model";
import { niceTicks } from "@/lib/overview-model";
import { cn } from "@/lib/utils";
import { day, eur, Panel, Swatch } from "./panel";

const thousands = (value: number) => `${value < 0 ? "−" : ""}€${Math.abs(value) >= 1_000_000 ? `${+(Math.abs(value) / 1_000_000).toFixed(1)}M` : `${Math.round(Math.abs(value) / 1000)}k`}`;
const LOOP_HREF = "#tasks?loop=forecast";
const DRIFT_HREF = "#tasks?loop=forecast&status=escalated";
const taskHref = (id: string) => `#task/${encodeURIComponent(id)}`;

const config = {
  actual: { label: "Actual", color: "var(--chart-balance)" },
  target: { label: "Target path", color: "var(--chart-buffer)" },
  forecast: { label: "Scout forecast", color: "var(--chart-forecast)" },
} satisfies ChartConfig;

/** CEO revenue target against Scout's realistic 12-month forecast, with weak points, actions and drift alerts. */
export function RevenuePlanSection({ plan, tasks, names }: { plan: RevenuePlan | null; tasks: AgentTask[]; names: Record<string, string> }) {
  const drift = driftAlerts(tasks);
  const rows = useMemo(() => plan ? revenueChartRows(plan) : [], [plan]);
  const banner = drift.length > 0 && <div id="forecast-plan-drift" role="alert" className="flex flex-wrap items-center gap-2 border-b border-danger-border bg-danger/10 px-4 py-2 text-[12px] text-danger">
    <AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" />
    <strong className="font-medium">{drift.length === 1 ? "1 action contradicts the plan" : `${drift.length} actions contradict the plan`}</strong>
    <span className="min-w-0 truncate text-danger/80">{drift[0].title}{drift.length > 1 ? " and more" : ""}</span>
    <a id="forecast-plan-drift-link" href={drift.length === 1 ? taskHref(drift[0].id) : DRIFT_HREF} className="ml-auto font-medium underline underline-offset-2">Review</a>
  </div>;

  if (!plan) return <Panel id="forecast-plan-overview" title="Revenue plan" meta="CEO target vs Scout's realistic forecast">
    {banner}
    <div role="status" className="flex flex-col items-center gap-3 px-4 py-12 text-center">
      <p className="max-w-md text-sm text-muted-foreground">No revenue forecast yet. Set an annual revenue target (default: 150% of the last 12 months) and Scout reviews invoices, bank, email and Slack daily to find the weakest points and plan a realistic year.</p>
      <Button id="forecast-plan-setup" size="sm" asChild><a href={LOOP_HREF}>Set up the Forecast loop</a></Button>
    </div>
  </Panel>;

  const short = isShort(plan);
  const share = Math.abs(gapShare(plan) * 100);
  const values = rows.flatMap(row => [row.actual, row.target, row.forecast]).filter((value): value is number => value !== null);
  // Revenue is never below zero: start the axis at €0 instead of the padded negative tick niceTicks adds.
  const ticks = values.length ? niceTicks(Math.min(0, ...values), Math.max(...values)).filter(tick => tick >= 0 || Math.min(...values) < 0) : [0, 1];
  const actions = plan.actions.slice(0, 6);
  const statusOf = new Map(tasks.map(task => [task.id, task.status]));

  return <Panel id="forecast-plan-overview" title="Revenue plan"
    meta={<>Target {eur.format(plan.targetAnnual)}{plan.targetSource === "default" ? " (default)" : ""} · Realistic {eur.format(plan.realisticAnnual)} · {" "}
      <span id="forecast-plan-gap" data-short={short} className={cn("font-medium", short ? "text-danger" : "text-success")}>
        {short ? `${eur.format(Math.abs(plan.gap))} short (${share.toFixed(0)}%)` : `${eur.format(plan.gap)} ahead`}
      </span>{plan.asOf ? ` · as of ${day(plan.asOf, true)}` : ""}</>}
    actions={<a id="forecast-plan-open-loop" href={LOOP_HREF} className="text-[12px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">Forecast loop</a>}>
    {banner}
    <div className="flex flex-1 flex-col gap-3 px-4 pt-3 pb-3" data-months={rows.length}>
      {rows.length > 0 ? <ChartContainer id="forecast-plan-chart" config={config} className="aspect-auto h-[248px] w-full">
        <ComposedChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} accessibilityLayer>
          <CartesianGrid vertical={false} strokeOpacity={0.6} />
          <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={16} tickMargin={6} />
          <YAxis width={52} tickLine={false} axisLine={false} domain={[ticks[0], ticks.at(-1)!]} ticks={ticks} tickFormatter={value => thousands(Number(value))} />
          <ChartTooltip cursor={{ fillOpacity: 0.3 }} content={<ChartTooltipContent
            formatter={(value, name) => value === null || value === undefined ? null : <span className="flex w-full justify-between gap-4"><span className="text-muted-foreground">{config[name as keyof typeof config]?.label ?? name}</span><span className="font-mono tabular-nums">{eur.format(Number(value))}</span></span>} />} />
          <Bar dataKey="actual" fill="var(--color-actual)" radius={[3, 3, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          <Line dataKey="target" type="monotone" stroke="var(--color-target)" strokeDasharray="5 4" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
          <Line dataKey="forecast" type="monotone" stroke="var(--color-forecast)" strokeWidth={2} dot={false} activeDot={{ r: 3 }} connectNulls isAnimationActive={false} />
        </ComposedChart>
      </ChartContainer> : <p className="py-10 text-center text-sm text-muted-foreground">No monthly figures in the latest review.</p>}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5"><Swatch color="var(--chart-balance)" />Actual (bank)</span>
        <span className="flex items-center gap-1.5"><Swatch color="var(--chart-buffer)" dashed />Target path</span>
        <span className="flex items-center gap-1.5"><Swatch color="var(--chart-forecast)" />Scout forecast</span>
        {(plan.constraints.noHiring || plan.constraints.maxNewMonthlyCost !== null) && <span className="ml-auto">
          {[plan.constraints.noHiring && "No new hires", plan.constraints.maxNewMonthlyCost !== null && `New costs ≤ ${eur.format(plan.constraints.maxNewMonthlyCost)}/mo`].filter(Boolean).join(" · ")}
        </span>}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <section aria-labelledby="forecast-plan-weak-title" className="rounded-lg border">
          <h3 id="forecast-plan-weak-title" className="border-b px-3 py-2 text-[12px] font-medium">Weakest points</h3>
          {plan.weakPoints.length ? <ul id="forecast-plan-weak-points" className="divide-y text-[12px]">
            {plan.weakPoints.slice(0, 5).map((point, index) => <li key={index} className="px-3 py-2">
              <span className="flex items-baseline justify-between gap-3"><span className="font-medium">{point.area}</span>
                {point.impactAnnual !== null && <span className="shrink-0 font-mono text-muted-foreground tabular-nums">{eur.format(point.impactAnnual)}/yr</span>}</span>
              {point.detail && <span className="mt-0.5 block text-muted-foreground">{point.detail}</span>}
            </li>)}
          </ul> : <p className="px-3 py-3 text-[12px] text-muted-foreground">No weak points found.</p>}
        </section>
        <section aria-labelledby="forecast-plan-actions-title" className="rounded-lg border">
          <h3 id="forecast-plan-actions-title" className="flex items-center justify-between border-b px-3 py-2 text-[12px] font-medium">Actions for the team
            <a href={LOOP_HREF} className="font-normal text-muted-foreground underline-offset-2 hover:text-foreground hover:underline">All</a></h3>
          {actions.length ? <ul id="forecast-plan-actions" className="divide-y text-[12px]">
            {actions.map(action => { const status = statusOf.get(action.taskId);
              return <li key={action.taskId}><a href={taskHref(action.taskId)} className="flex items-baseline gap-2 px-3 py-2 hover:bg-muted/40">
                <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{action.taskId}</span>
                <span className={cn("min-w-0 flex-1", (status === "done" || status === "cancelled") && "text-muted-foreground line-through")}>{action.title}</span>
                {action.ownerId && <span className="shrink-0 text-muted-foreground">{names[action.ownerId] ?? action.ownerId}</span>}
              </a></li>; })}
          </ul> : <p className="px-3 py-3 text-[12px] text-muted-foreground">No actions yet.</p>}
        </section>
      </div>
    </div>
  </Panel>;
}
