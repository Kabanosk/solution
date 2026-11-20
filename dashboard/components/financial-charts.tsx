"use client";

import { useRef, useState, type KeyboardEvent } from "react";
import { CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ReferenceLine, XAxis, YAxis } from "recharts";
import type { DashboardData } from "@/lib/finance";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PlotPanSurface } from "@/components/plot-pan-surface";
import { forecastViewport, panViewport, zoomViewport, type PlotViewport } from "@/lib/plot-viewport";

const millisecondsPerDay = 86_400_000;
const currency = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });
const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const forecastConfig = { balance: { label: "Predicted balance", color: "var(--chart-forecast)" } } satisfies ChartConfig;

// Keep the server and browser labels identical even with limited Node.js locale data.
function formatDate(date: Date, includeYear = true) {
  const label = `${date.getUTCDate()} ${monthNames[date.getUTCMonth()]}`;
  return includeYear ? `${label} ${date.getUTCFullYear()}` : label;
}

export function FinancialCharts({ data }: { data: DashboardData }) {
  const firstDate = Date.parse(data.forecast?.asOf ?? data.asOf);
  const dateForDay = (day: number) => new Date(firstDate + Math.round(day) * millisecondsPerDay);
  const [view, setView] = useState("forecast");
  const [period, setPeriod] = useState<"year" | "q4">("year");
  const points = data.forecast?.points ?? [];
  const initialViewport = forecastViewport(points, data.forecast?.safetyBuffer ?? 0);
  const [customViewport, setViewport] = useState<PlotViewport | null>(null);
  const viewport = customViewport ?? initialViewport;
  const bounds = viewport.x;
  const plotRegion = useRef<HTMLDivElement>(null);
  const expenses = data[period];
  const pieData = expenses.expenses.map((category, index) => ({ ...category, key: `category${index}`, fill: category.color }));
  const pieConfig = Object.fromEntries(pieData.map((category) => [category.key, { label: category.name, color: category.color }])) satisfies ChartConfig;

  function zoom(factor: number, anchor?: { x: number; y: number }) {
    setViewport(current => zoomViewport(current ?? initialViewport, factor, anchor));
  }

  function pan(dx: number, dy: number) {
    setViewport(current => panViewport(current ?? initialViewport, dx, dy));
  }

  function handleChartKey(event: KeyboardEvent<HTMLDivElement>) {
    if (view !== "forecast" || !data.forecast || event.altKey || event.metaKey) return;
    const target = event.target as HTMLElement;
    if (target.closest('input, textarea, select, [contenteditable="true"]')) return;
    if (!target.closest("#lantern-forecast-panel")) return;
    if (event.ctrlKey && ["+", "=", "-", "0"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "0") setViewport(null);
      else zoom(event.key === "-" ? 1.25 : 0.8);
    } else if (!event.ctrlKey && plotRegion.current?.contains(target) && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      event.stopPropagation();
      pan(event.key === "ArrowLeft" ? 0.1 : event.key === "ArrowRight" ? -0.1 : 0,
        event.key === "ArrowUp" ? -0.1 : event.key === "ArrowDown" ? 0.1 : 0);
    }
  }

  return (
    <Card id="lantern-financial-charts" aria-labelledby="lantern-financial-charts-title" className="gap-5">
      <CardHeader id="lantern-financial-charts-header" className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div id="lantern-financial-charts-heading" className="space-y-1.5">
          <CardTitle id="lantern-financial-charts-title" className="text-base font-medium">Financial outlook</CardTitle>
          <CardDescription id="lantern-financial-charts-description">Main account · EUR · through {data.asOf}</CardDescription>
        </div>
        <Badge id="lantern-chart-source-badge" variant="outline" className="w-fit border-warning-border bg-warning-muted font-normal text-warning">
          {view === "forecast" ? data.forecast?.model ?? "Forecast unavailable" : "Company bank records"}
        </Badge>
      </CardHeader>
      <CardContent id="lantern-financial-charts-content" className="min-w-0">
        <Tabs id="lantern-financial-chart-view" value={view} onValueChange={setView}
          data-chart-view={view} data-chart-period={period} data-expense-total={expenses.totalExpenses.toFixed(2)}
          data-plot-x-min={bounds[0]} data-plot-x-max={bounds[1]} data-plot-y-min={viewport.y[0]} data-plot-y-max={viewport.y[1]}
          onKeyDownCapture={handleChartKey} className="min-w-0 gap-5">
          <TabsList id="lantern-chart-tabs" aria-label="Financial chart" className="h-auto w-full max-w-full flex-wrap justify-start gap-2 p-2 group-data-[orientation=horizontal]/tabs:h-auto sm:w-fit">
            <TabsTrigger id="lantern-forecast-tab" aria-controls="lantern-forecast-panel" value="forecast" className="h-auto min-h-11 min-w-0 flex-auto whitespace-normal px-4 py-3">Cash balance forecast</TabsTrigger>
            <TabsTrigger id="lantern-expenses-tab" aria-controls="lantern-expense-panel" value="expenses" className="h-auto min-h-11 min-w-0 flex-auto whitespace-normal px-4 py-3">Expense mix</TabsTrigger>
          </TabsList>
          <TabsContent id="lantern-forecast-panel" aria-labelledby="lantern-forecast-tab" value="forecast" className="min-w-0 space-y-4">
            {data.forecast ? <><div id="lantern-forecast-controls" className="flex flex-wrap items-center gap-2">
              <Button id="lantern-chart-zoom-in" variant="outline" size="sm" className="min-h-11" onClick={() => zoom(0.8)}>Zoom in</Button>
              <Button id="lantern-chart-zoom-out" variant="outline" size="sm" className="min-h-11" onClick={() => zoom(1.25)}>Zoom out</Button>
              <Button id="lantern-chart-reset" variant="ghost" size="sm" className="min-h-11" onClick={() => setViewport(null)}>Reset</Button>
              <div id="lantern-chart-pan-controls" role="group" aria-label="Pan forecast" className="flex flex-wrap gap-2">
                <Button id="lantern-chart-pan-left" variant="outline" size="sm" className="min-h-11" onClick={() => pan(0.1, 0)}>Pan left</Button>
                <Button id="lantern-chart-pan-right" variant="outline" size="sm" className="min-h-11" onClick={() => pan(-0.1, 0)}>Pan right</Button>
                <Button id="lantern-chart-pan-up" variant="outline" size="sm" className="min-h-11" onClick={() => pan(0, -0.1)}>Pan up</Button>
                <Button id="lantern-chart-pan-down" variant="outline" size="sm" className="min-h-11" onClick={() => pan(0, 0.1)}>Pan down</Button>
              </div>
              <span id="lantern-chart-date-range" className="text-xs leading-5 text-muted-foreground" aria-live="polite">{formatDate(dateForDay(bounds[0]))} – {formatDate(dateForDay(bounds[1]))}</span>
            </div>
            <div id="lantern-forecast-focus-region" ref={plotRegion} tabIndex={0} role="region" aria-label="Interactive cash forecast"
              aria-describedby="lantern-forecast-summary" aria-keyshortcuts="Control+Plus Control+Minus Control+0 ArrowLeft ArrowRight ArrowUp ArrowDown"
              className="max-h-[60svh] min-w-0 max-w-full overflow-auto rounded-md border p-2 outline-offset-4 focus-visible:outline-2 focus-visible:outline-ring"
              onPointerDown={(event) => event.currentTarget.focus({ preventScroll: true })}>
              <ChartContainer id="lantern-cash-forecast-chart" config={forecastConfig} className="h-[380px] w-full min-w-[640px] aspect-auto">
                <LineChart id="lantern-cash-forecast-line-chart" data={points} margin={{ top: 16, right: 12, bottom: 20, left: 8 }} accessibilityLayer>
                  <CartesianGrid id="lantern-cash-forecast-grid" vertical={false} />
                  <XAxis id="lantern-cash-forecast-x-axis" dataKey="day" type="number" domain={bounds} allowDataOverflow tickLine={false} axisLine={false} minTickGap={28}
                    tickFormatter={(day) => formatDate(dateForDay(Number(day)), false)} label={{ value: "Date", position: "insideBottom", offset: -12, fill: "var(--muted-foreground)" }} />
                  <YAxis id="lantern-cash-forecast-y-axis" width={66} tickLine={false} axisLine={false} domain={viewport.y} allowDataOverflow
                    tickFormatter={(balance) => `€${(Number(balance) / 1_000).toFixed(0)}k`} />
                  <ChartTooltip content={<ChartTooltipContent labelFormatter={(_label, payload) => formatDate(dateForDay(Number(payload?.[0]?.payload?.day ?? 0)))} formatter={(value) => currency.format(Number(value))} />} />
                  <ReferenceLine id="lantern-cash-safety-buffer" y={data.forecast.safetyBuffer} ifOverflow="hidden" stroke="var(--chart-buffer)" strokeDasharray="3 3" />
                  <Line id="lantern-cash-forecast-line" dataKey="balance" name="Predicted balance" type="linear" stroke="var(--color-balance)" strokeWidth={2}
                    strokeDasharray="5 4" dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
                  <PlotPanSurface viewport={viewport} onPan={setViewport} onZoom={zoom} onReset={() => setViewport(null)} onFocus={() => plotRegion.current?.focus({ preventScroll: true })} />
                </LineChart>
              </ChartContainer>
            </div>
            <p id="lantern-forecast-summary" className="text-xs leading-6 text-muted-foreground">Drag the plot to pan in any direction. Hover and Ctrl + scroll up/down to zoom at the pointer. With the chart focused, Ctrl + / Ctrl − zoom both axes and arrow keys pan. Double-click, Ctrl 0 or Reset fits all data. Scrollbars move around the chart on small screens. Orange: predicted balance. Red: safety buffer ({currency.format(data.forecast.safetyBuffer)}). Dates on X; EUR on Y.</p></> : <p id="lantern-forecast-unavailable" role="status" className="py-12 text-sm text-muted-foreground">The forecast is temporarily unavailable. Bank totals and expenses remain available.</p>}
          </TabsContent>
          <TabsContent id="lantern-expense-panel" aria-labelledby="lantern-expenses-tab" value="expenses" className="min-w-0 space-y-4">
            <div id="lantern-expense-controls" className="flex flex-wrap items-center justify-between gap-3">
              <div id="lantern-expense-period-controls" role="group" aria-label="Expense period" className="flex gap-2">
                <Button id="lantern-expense-period-year" size="sm" variant={period === "year" ? "secondary" : "outline"} aria-pressed={period === "year"} className="min-h-11" onClick={() => setPeriod("year")}>Year {data.yearLabel}</Button>
                <Button id="lantern-expense-period-q4" size="sm" variant={period === "q4" ? "secondary" : "outline"} aria-pressed={period === "q4"} className="min-h-11" onClick={() => setPeriod("q4")}>Q4 {data.yearLabel}</Button>
              </div>
              <span id="lantern-expense-transaction-count" className="text-xs text-muted-foreground">{expenses.transactions.toLocaleString("en-IE")} transactions</span>
            </div>
            <div id="lantern-expense-visualization" className="grid min-h-[340px] min-w-0 items-center gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <ChartContainer id="lantern-expense-pie-chart" config={pieConfig} className="mx-auto h-[280px] w-full max-w-[340px] aspect-auto">
                <PieChart id="lantern-expense-pie-view" accessibilityLayer>
                  <ChartTooltip cursor={false} content={<ChartTooltipContent hideLabel nameKey="key" formatter={(value, _name, item) => `${item.payload.name}: ${currency.format(Number(value))} (${Number(item.payload.percentage).toFixed(1)}%)`} />} />
                  <Pie id="lantern-expense-pie" data={pieData} dataKey="amount" nameKey="key" innerRadius="52%" outerRadius="85%" strokeWidth={2} stroke="var(--background)" isAnimationActive={false}>
                    {pieData.map((category) => <Cell id={`lantern-expense-cell-${category.key}`} key={category.key} fill={category.color} />)}
                  </Pie>
                </PieChart>
              </ChartContainer>
              <div id="lantern-expense-legend" className="min-w-0 space-y-1">
                {pieData.map((category) => (
                  <div id={`lantern-expense-legend-${category.key}`} key={category.key} className="flex items-center justify-between gap-3 border-b border-border/60 py-3 text-sm">
                    <span id={`lantern-expense-label-${category.key}`} className="flex min-w-0 items-center gap-2">
                      <span id={`lantern-expense-swatch-${category.key}`} aria-hidden="true" className="size-2 shrink-0 rounded-sm" style={{ backgroundColor: category.color }} />
                      {category.name}
                    </span>
                    <span id={`lantern-expense-value-${category.key}`} className="shrink-0 text-right tabular-nums">{category.percentage.toFixed(0)}%<span id={`lantern-expense-amount-${category.key}`} className="mt-1 block text-xs text-muted-foreground">{currency.format(category.amount)}</span></span>
                  </div>
                ))}
              </div>
            </div>
            <p id="lantern-expense-total" className="text-sm tabular-nums" aria-live="polite">Total out: {currency.format(expenses.totalExpenses)}<span id="lantern-expense-source-note" className="mt-2 block text-xs leading-6 text-muted-foreground">Calculated from company bank transactions through {data.asOf}. Q4 includes recorded bookings from October onwards.</span></p>
          </TabsContent>
        </Tabs>
      </CardContent>
      <CardFooter id="lantern-financial-charts-footer" className="block border-t pt-4">
        <details id="lantern-chart-data" className="text-sm">
          <summary id="lantern-chart-data-summary" className="w-fit cursor-pointer rounded-sm focus-visible:outline-2 focus-visible:outline-ring">Chart data and controls</summary>
          <p id="lantern-chart-controls-explanation" className="mt-3 text-sm leading-6 text-muted-foreground">Use the tabs to choose a chart. Drag the forecast to move both axes, or use the pan buttons. Hold Ctrl and scroll up/down over the plot to zoom both axes around the pointer, without clicking first. Ctrl + / Ctrl − zoom both axes while focus is inside the forecast viewer; arrow keys pan the focused plot. Double-click, Ctrl 0 or Reset restores the full forecast and safety buffer. Browser shortcuts elsewhere keep their usual behavior. Hover a chart point for its value. Animations are disabled.</p>
          <p id="lantern-chart-forecast-provenance" className="mt-2 text-sm leading-6 text-muted-foreground">The forecast comes from the CFO liquidity model trained on the database records. Predictions are estimates. No synthetic fallback is substituted when the model is unavailable.</p>
          {([['Year', data.year], ['Q4', data.q4]] as const).map(([label, values]) => (
            <p id={`lantern-chart-data-${label.toLowerCase()}`} key={label} className="mt-2 text-xs leading-6 text-muted-foreground">{label}: {values.expenses.map((category) => `${category.name} ${currency.format(category.amount)} (${category.percentage.toFixed(0)}%)`).join('; ')}</p>
          ))}
        </details>
      </CardFooter>
    </Card>
  );
}
