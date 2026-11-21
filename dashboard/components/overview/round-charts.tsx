"use client";

import { Cell, Pie, PieChart } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { QuarterSpend } from "@/lib/finance-model";
import { actionSummary, donutSlices, notableActions, SCOUT_OUTCOME_LABEL, scoutRing, type ScoutOutcome } from "@/lib/overview-model";
import { shortDate, type AgentTask } from "@/lib/scout-model";
import { cn } from "@/lib/utils";
import { day, eur, eurCents, Panel, Swatch } from "./panel";

// Greens, orange and greys only (AGENTS.md palette); spend without a team is always grey.
const TEAM_COLORS = ["var(--chart-balance)", "var(--chart-forecast)", "#42c994", "#929292", "#277757", "#626262"];
const NO_TEAM_COLOR = "#b0b0b0";

function Donut({ id, data, config, label, children }: { id: string; data: { key: string; value: number; fill: string }[]; config: ChartConfig; label: string; children: React.ReactNode }) {
  return <div className="relative mx-auto size-[136px] shrink-0 xl:size-[160px]">
    <ChartContainer id={id} config={config} className="aspect-square size-full">
      <PieChart accessibilityLayer>
        <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel nameKey="key" />} />
        <Pie data={data} dataKey="value" nameKey="key" innerRadius="70%" outerRadius="100%" paddingAngle={data.length > 1 ? 1.5 : 0}
          stroke="var(--card)" strokeWidth={1} isAnimationActive={false}>
          {data.map(slice => <Cell key={slice.key} fill={slice.fill} />)}
        </Pie>
      </PieChart>
    </ChartContainer>
    <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center" aria-label={label}>{children}</div>
  </div>;
}

/** Where the money goes: spend per team this quarter with each team's budget use. */
export function TeamSpendDonut({ spend }: { spend: QuarterSpend }) {
  const { slices, total } = donutSlices(spend.teams);
  let colorIndex = 0;
  const colored = slices.map(slice => ({ ...slice, key: slice.id ?? "no-team", color: slice.id === null ? NO_TEAM_COLOR : TEAM_COLORS[colorIndex++ % TEAM_COLORS.length] }));
  const pie = colored.filter(slice => slice.spent > 0).map(slice => ({ key: slice.key, value: slice.spent, fill: slice.color }));
  const config = Object.fromEntries(colored.map(slice => [slice.key, { label: slice.name, color: slice.color }])) satisfies ChartConfig;
  return <Panel id="scout-team-spend" title="Where the money goes" meta={`${spend.label} to date · ${day(spend.start)} – ${day(spend.through, true)} · against 3 monthly budgets`}>
    <div className="flex flex-1 flex-col items-center gap-4 px-4 py-4 sm:flex-row sm:items-start" data-total={total.toFixed(2)}>
      {total > 0 ? <Donut id="scout-team-spend-donut" data={pie} config={config} label={`Total spent ${eur.format(total)}`}>
        <span className="font-mono text-[17px] font-medium tracking-tight">{eur.format(total)}</span>
        <span className="text-[11px] text-muted-foreground">spent</span>
      </Donut> : <p className="text-sm text-muted-foreground">No spending booked this quarter yet.</p>}
      <table className="w-full min-w-0 table-fixed text-[12px]" aria-label={`Spend per team, ${spend.label}`}>
        <colgroup><col /><col className="w-[84px]" /><col className="w-[48px]" /></colgroup>
        <thead className="sr-only"><tr><th>Team</th><th>Spent</th><th>Budget used</th></tr></thead>
        <tbody>
          {colored.map(slice => <tr key={slice.key} className="border-b last:border-b-0" data-spent={slice.spent.toFixed(2)}>
            <th scope="row" className="py-1.5 pr-2 text-left font-normal"><span className="flex min-w-0 items-center gap-2"><Swatch color={slice.color} /><span className="truncate">{slice.name}</span></span></th>
            <td className="py-1.5 pr-2 text-right font-mono tabular-nums">{eurCents.format(slice.spent)}</td>
            <td className="py-1.5 text-right whitespace-nowrap">{slice.budgetShare === null
              ? <span className="text-[11px] text-muted-foreground" title="Unassigned spend (tax, social security, partner payouts) has no budget">—</span>
              : <span title={`Budget ${eur.format(slice.budget!)} for the quarter`}
                  className={cn("inline-block rounded-full border px-1.5 py-px font-mono text-[10.5px] tabular-nums",
                    slice.over ? "border-danger-border bg-danger/10 text-danger" : "text-muted-foreground")}>
                  {Math.round(slice.budgetShare * 100)}%</span>}{slice.over && <span className="sr-only"> over budget</span>}</td>
          </tr>)}
        </tbody>
        <tfoot><tr className="border-t"><th scope="row" className="pt-2 text-left font-medium">Total</th><td className="pt-2 pr-2 text-right font-mono font-medium tabular-nums">{eurCents.format(total)}</td><td /></tr></tfoot>
      </table>
    </div>
    <p className="px-4 pb-3 text-[11px] text-muted-foreground">Pills: share of the team&apos;s budget for the quarter used so far. Red means over budget.</p>
  </Panel>;
}

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const OUTCOME_COLOR: Record<ScoutOutcome, string> = { alone: "var(--chart-balance)", told: "#42c994", asked: "var(--chart-forecast)" };
const OUTCOME_HINT: Record<ScoutOutcome, string> = {
  alone: "Matched invoices from files, email or chat without anyone",
  told: "Acted, then sent you a note",
  asked: "Waited for your approval or a Slack answer",
};

/** What Scout did this month: tasks by how they ended, plus the latest notable actions. */
export function ScoutActivityRing({ tasks, now }: { tasks: AgentTask[]; now: string }) {
  const ring = scoutRing(tasks, now);
  const order: ScoutOutcome[] = ["alone", "told", "asked"];
  const data = order.filter(key => ring.counts[key] > 0).map(key => ({ key, value: ring.counts[key], fill: OUTCOME_COLOR[key] }));
  const config = Object.fromEntries(order.map(key => [key, { label: SCOUT_OUTCOME_LABEL[key], color: OUTCOME_COLOR[key] }])) satisfies ChartConfig;
  const actions = notableActions(tasks);
  const month = MONTH_NAMES[Number(now.slice(5, 7)) - 1];
  return <Panel id="scout-activity" title="What Scout did" meta={`Tasks opened in ${month}, by how they ended · chat answers not counted`}>
    <div className="flex flex-1 flex-col gap-4 px-4 py-4">
      <div className="flex flex-col items-center gap-4 sm:flex-row sm:items-start">
        {ring.total > 0 ? <Donut id="scout-activity-ring" data={data} config={config} label={`${ring.total} tasks ended this month`}>
          <span className="font-mono text-[22px] leading-none font-medium tracking-tight">{ring.total}</span>
          <span className="mt-1 text-[11px] text-muted-foreground">tasks ended</span>
        </Donut> : <p className="text-sm text-muted-foreground">No tasks ended this month yet.</p>}
        <ul className="w-full min-w-0 text-[12px]" aria-label="Tasks by how they ended" data-counts={order.map(key => ring.counts[key]).join(",")}>
          {order.map(key => <li key={key} className="flex items-start gap-2 border-b py-1.5 last:border-b-0">
            <span className="mt-1"><Swatch color={OUTCOME_COLOR[key]} /></span>
            <span className="min-w-0 flex-1"><span className="block">{SCOUT_OUTCOME_LABEL[key]}</span><span className="block truncate text-[11px] text-muted-foreground">{OUTCOME_HINT[key]}</span></span>
            <span className="font-mono tabular-nums">{ring.counts[key]}</span>
          </li>)}
          {ring.running > 0 && <li className="pt-1.5 text-[11px] text-muted-foreground">{ring.running} more still running, counted once they end</li>}
        </ul>
      </div>
      {actions.length > 0 && <div>
        <h3 className="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Latest</h3>
        <ol className="relative space-y-2 border-l pl-3">
          {actions.map(action => <li key={action.id} className="relative text-[12px]">
            <span aria-hidden="true" className="absolute top-1.5 -left-[16.5px] size-2 rounded-full ring-2 ring-card" style={{ backgroundColor: OUTCOME_COLOR[action.how] }} />
            <a href={`#task/${encodeURIComponent(action.id)}`} className="group flex min-w-0 items-baseline gap-2 rounded-sm focus-visible:outline-2 focus-visible:outline-ring">
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{action.id}</span>
              <span className="min-w-0 flex-1 truncate group-hover:underline" title={action.outcome ?? undefined}>{actionSummary(action)}</span>
              <span className="shrink-0 text-[11px] text-muted-foreground">{shortDate(action.closedAt ?? action.updatedAt)}</span>
            </a>
            <span className="sr-only">{SCOUT_OUTCOME_LABEL[action.how]}: {action.title}</span>
          </li>)}
        </ol>
      </div>}
    </div>
  </Panel>;
}
