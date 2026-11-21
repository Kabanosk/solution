"use client";

import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { Forecast } from "@/lib/finance-model";
import {
  BUFFER_STATUS_LABEL, coverage, formatCoverage, gaugeStatus, niceTicks, TIGHT_UNDER, withWhatIf, type BufferStatus, type WhatIf,
} from "@/lib/overview-model";
import { day, eur, Panel, Segmented, Swatch } from "./panel";

type Range = "90d" | "12m";
type Scenario = "base" | "whatif";

const thousands = (value: number) => `${value < 0 ? "−" : ""}€${Math.abs(value) >= 1_000_000 ? `${+(Math.abs(value) / 1_000_000).toFixed(1)}M` : `${Math.round(Math.abs(value) / 1000)}k`}`;

const config = {
  base: { label: "Base case", color: "var(--chart-balance)" },
  scenario: { label: "What if", color: "var(--chart-forecast)" },
} satisfies ChartConfig;

/** Cash forecast with range and scenario switchers, next to the safety-buffer gauge for the same range. */
export function ForecastSection({ quarter, year, whatIf }: { quarter: Forecast | null; year: Forecast | null; whatIf: WhatIf | null }) {
  const [range, setRange] = useState<Range>("90d");
  const [scenario, setScenario] = useState<Scenario>("base");
  const forecast = range === "90d" ? quarter : year;
  return <section aria-label="Cash forecast and safety buffer" className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
    <ForecastCard forecast={forecast} range={range} onRange={setRange} scenario={whatIf ? scenario : "base"} onScenario={setScenario} whatIf={whatIf} />
    <BufferGauge forecast={forecast ?? quarter} range={forecast ? range : "90d"} />
  </section>;
}

function ForecastCard({ forecast, range, onRange, scenario, onScenario, whatIf }: {
  forecast: Forecast | null; range: Range; onRange: (range: Range) => void;
  scenario: Scenario; onScenario: (scenario: Scenario) => void; whatIf: WhatIf | null;
}) {
  const showScenario = scenario === "whatif" && whatIf !== null;
  const data = useMemo(() => {
    if (!forecast) return [];
    const alternative = showScenario ? withWhatIf(forecast.points, whatIf) : null;
    return forecast.points.map((point, index) => ({ day: point.day, date: point.date, base: point.balance, ...(alternative && { scenario: alternative[index].balance }) }));
  }, [forecast, showScenario, whatIf]);
  const lastDate = forecast?.points.at(-1)?.date ?? "";
  const outOfRange = showScenario && whatIf && lastDate && whatIf.on > lastDate;
  const scenarioLow = showScenario ? data.reduce((low, point) => point.scenario! < low.balance ? { date: point.date, balance: point.scenario! } : low, { date: "", balance: Infinity }) : null;
  const values = data.flatMap(point => [point.base, point.scenario ?? point.base]);
  const ticks = forecast ? niceTicks(Math.min(...values, forecast.safetyBuffer), Math.max(...values, forecast.safetyBuffer)) : [0, 1];

  return <Panel id="scout-forecast" title="Cash forecast"
    meta={forecast ? `Predicted balance · ${forecast.model} · from ${day(forecast.asOf, true)}` : "Forecast unavailable"}
    actions={<>
      <Segmented id="scout-forecast-range" label="Forecast range" value={range} onChange={onRange}
        options={[{ value: "90d", label: "90 days" }, { value: "12m", label: "12 months" }]} />
      {whatIf && <Segmented id="scout-forecast-scenario" label="Scenario" value={scenario} onChange={onScenario}
        options={[{ value: "base", label: "Base case" }, { value: "whatif", label: `What if: ${whatIf.what}`, title: `What if: ${whatIf.label} (${whatIf.taskId})` }]} />}
    </>}>
    {forecast ? <div className="flex flex-1 flex-col gap-3 px-4 pt-3 pb-3" data-range={range} data-scenario={showScenario ? "whatif" : "base"} data-points={data.length}>
      <ChartContainer id="scout-forecast-chart" config={config} className="aspect-auto h-[248px] w-full">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} accessibilityLayer>
          <CartesianGrid vertical={false} strokeOpacity={0.6} />
          <XAxis dataKey="day" type="number" domain={["dataMin", "dataMax"]} tickLine={false} axisLine={false} minTickGap={36} tickMargin={6}
            tickFormatter={value => day(new Date(Date.parse(forecast.asOf) + Number(value) * 86_400_000).toISOString())} />
          <YAxis width={52} tickLine={false} axisLine={false} domain={[ticks[0], ticks.at(-1)!]} ticks={ticks} tickFormatter={value => thousands(Number(value))} />
          <ChartTooltip cursor={{ strokeDasharray: "3 3" }} content={<ChartTooltipContent
            labelFormatter={(_label, payload) => day(String(payload?.[0]?.payload?.date ?? forecast.asOf), true)}
            formatter={(value, name) => <span className="flex w-full justify-between gap-4"><span className="text-muted-foreground">{config[name as keyof typeof config]?.label ?? name}</span><span className="font-mono tabular-nums">{eur.format(Number(value))}</span></span>} />} />
          <ReferenceLine y={forecast.safetyBuffer} stroke="var(--chart-buffer)" strokeDasharray="4 4" strokeWidth={1} ifOverflow="extendDomain" />
          <Line dataKey="base" type="monotone" stroke="var(--color-base)" strokeWidth={showScenario ? 1.25 : 2} strokeOpacity={showScenario ? 0.45 : 1}
            dot={false} activeDot={{ r: 3 }} isAnimationActive={false} />
          {showScenario && <Line dataKey="scenario" type="monotone" stroke="var(--color-scenario)" strokeWidth={2} dot={false} activeDot={{ r: 3 }} isAnimationActive={false} />}
        </LineChart>
      </ChartContainer>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span className="flex items-center gap-1.5"><Swatch color="var(--chart-balance)" />Base case</span>
        {showScenario && <span className="flex items-center gap-1.5"><Swatch color="var(--chart-forecast)" />What if: {whatIf!.label}</span>}
        <span className="flex items-center gap-1.5"><Swatch color="var(--chart-buffer)" dashed />Safety buffer {eur.format(forecast.safetyBuffer)}</span>
        <span className="ml-auto">Hover the chart for a day&apos;s balance</span>
      </div>
      <p id="scout-forecast-summary" aria-live="polite" className="text-[12px] text-muted-foreground">
        {showScenario && whatIf
          ? outOfRange
            ? <>The {whatIf.what} ({eur.format(whatIf.amount)}) lands on {day(whatIf.on, true)}, after this range. Switch to 12 months to see it.</>
            : <>With the {whatIf.what} ({eur.format(whatIf.amount)} on {day(whatIf.on, true)}) the balance bottoms out at <strong className="font-medium text-foreground">{eur.format(scenarioLow!.balance)}</strong> on {day(scenarioLow!.date, true)}{scenarioLow!.balance < forecast.safetyBuffer ? ", below the safety buffer" : ", above the safety buffer"}. <a href={`#task/${encodeURIComponent(whatIf.taskId)}`} className="underline underline-offset-2 hover:text-foreground">{whatIf.taskId}</a></>
          : <>Base case: the balance moves from {eur.format(forecast.openingBalance)} today to {eur.format(forecast.points.at(-1)!.balance)} on {day(lastDate, true)}.</>}
      </p>
    </div> : <p role="status" className="px-4 py-16 text-center text-sm text-muted-foreground">The forecast is temporarily unavailable. Bank records stay available.</p>}
  </Panel>;
}

const STATUS_COLOR: Record<BufferStatus, string> = { healthy: "var(--chart-balance)", tight: "var(--warning-mark)", below: "var(--danger)" };
const SCALE = 3; // the dial runs from 0 to 3× the buffer; more is shown as "3×+" on the dial and exactly in the text

/** Semicircle of cash against the safety buffer: zones labelled, today's coverage as the filled arc, the lowest point ahead as a marker. */
function BufferGauge({ forecast, range }: { forecast: Forecast | null; range: Range }) {
  if (!forecast) return <Panel id="scout-buffer" title="Safety buffer"><p role="status" className="px-4 py-16 text-center text-sm text-muted-foreground">Needs the forecast, which is unavailable right now.</p></Panel>;
  const { openingBalance: balance, safetyBuffer: buffer, lowest } = forecast;
  const ratio = coverage(balance, buffer);
  const lowRatio = coverage(lowest.balance, buffer);
  const status = gaugeStatus(balance, lowest.balance, buffer);
  const horizon = range === "90d" ? "next 90 days" : "next 12 months";
  const sentence = `Cash today ${eur.format(balance)} covers ${formatCoverage(ratio)} the safety buffer of ${eur.format(buffer)}. `
    + `Lowest balance in the ${horizon}: ${eur.format(lowest.balance)} on ${day(lowest.date, true)} (${formatCoverage(lowRatio)} the buffer). Status: ${BUFFER_STATUS_LABEL[status]}.`;

  const cx = 110, cy = 104, r = 84;
  const point = (value: number, radius = r) => {
    const angle = Math.PI * (1 - Math.min(Math.max(value, 0), SCALE) / SCALE);
    return [cx + radius * Math.cos(angle), cy - radius * Math.sin(angle)] as const;
  };
  const arc = (from: number, to: number) => { const [x1, y1] = point(from); const [x2, y2] = point(to); return `M ${x1} ${y1} A ${r} ${r} 0 0 1 ${x2} ${y2}`; };
  const [lx1, ly1] = point(lowRatio, r - 11);
  const [lx2, ly2] = point(lowRatio, r + 11);
  const ticks: [number, string][] = [[0, "0"], [1, "1× buffer"], [TIGHT_UNDER, "1.5×"], [SCALE, "3×"]];

  return <Panel id="scout-buffer" title="Safety buffer" meta={`One month of fixed costs · ${horizon}`}
    actions={<span className="flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium" data-status={status}><span aria-hidden="true" className="size-1.5 rounded-full" style={{ background: STATUS_COLOR[status] }} />{BUFFER_STATUS_LABEL[status]}</span>}>
    <div className="flex flex-1 flex-col gap-3 px-4 pt-3 pb-4" data-ratio={ratio.toFixed(3)} data-lowest={lowest.balance} data-buffer={buffer}>
      <figure className="relative mx-auto w-full max-w-[260px]">
        <svg viewBox="0 0 220 122" role="img" aria-label={sentence} className="w-full overflow-visible">
          <path d={arc(0, SCALE)} fill="none" stroke="var(--muted)" strokeWidth={12} />
          {ratio > 0 && <path d={arc(0, Math.min(ratio, SCALE))} fill="none" stroke={STATUS_COLOR[status]} strokeWidth={12} strokeLinecap="butt" />}
          {[1, TIGHT_UNDER].map((value) => { const [a, b] = point(value, r - 6); const [c, d] = point(value, r + 6);
            return <line key={value} x1={a} y1={b} x2={c} y2={d} stroke="var(--card)" strokeWidth={2} />; })}
          <line x1={lx1} y1={ly1} x2={lx2} y2={ly2} stroke="var(--foreground)" strokeWidth={2} />
          {ticks.map(([value, label]) => {
            const [x, y] = point(value, r + 20);
            return <text key={label} x={x} y={y + 3} textAnchor={value === 0 ? "end" : value === SCALE ? "start" : "middle"} className="fill-muted-foreground text-[9px]">{label}</text>;
          })}
        </svg>
        <figcaption className="absolute inset-x-0 bottom-0 text-center" aria-hidden="true">
          <span className="block font-mono text-[28px] leading-none font-medium tracking-tight">{formatCoverage(ratio)}</span>
          <span className="mt-1 block text-[11px] text-muted-foreground">covers {formatCoverage(ratio)} the buffer</span>
        </figcaption>
      </figure>
      <dl className="divide-y rounded-lg border text-[12px]">
        <div className="flex items-center justify-between gap-3 px-3 py-2"><dt className="flex items-center gap-2 text-muted-foreground"><Swatch color={STATUS_COLOR[status]} />Cash today</dt><dd className="font-mono tabular-nums">{eur.format(balance)}</dd></div>
        <div className="flex items-center justify-between gap-3 px-3 py-2"><dt className="flex items-center gap-2 text-muted-foreground"><Swatch color="var(--chart-buffer)" dashed />Safety buffer</dt><dd className="font-mono tabular-nums">{eur.format(buffer)}</dd></div>
        <div className="flex items-center justify-between gap-3 px-3 py-2"><dt className="flex items-center gap-2 text-muted-foreground"><span aria-hidden="true" className="h-3 w-0.5 shrink-0 bg-foreground" />Lowest ahead</dt>
          <dd className="text-right whitespace-nowrap"><span className="text-muted-foreground">{day(lowest.date)} · </span><span className="font-mono tabular-nums">{eur.format(lowest.balance)}</span></dd></div>
      </dl>
      <p className="text-[11px] leading-5 text-muted-foreground">Below 1× is below the buffer, under {TIGHT_UNDER}× is tight. The status takes the worse of today and the lowest point ahead.</p>
    </div>
  </Panel>;
}
