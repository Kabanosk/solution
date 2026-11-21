import { test } from "node:test";
import assert from "node:assert/strict";
import {
  backendError, driftAlerts, gapShare, isShort, moneyValue, monthLabel, openForecastActions, parsePlanInput, parseRevenuePlan, revenueChartRows, sanitizePlan,
} from "../lib/revenue-plan-model.ts";

const evidence = {
  kind: "forecast_plan", as_of: "2026-11-21", currency: "EUR",
  trailing_revenue: "300000.00", target_annual: "450000.00", target_source: "default", realistic_annual: "360000.00", gap: "-90000.00",
  months: [
    { month: "2026-11-01", actual: "26000.00", target: null, forecast: null },
    { month: "2026-10-01", actual: "25000.00", target: null, forecast: null },
    { month: "2026-12-01", actual: null, target: "37500.00", forecast: "30000.00" },
    { month: "garbage", actual: "1" },
  ],
  weak_points: [{ area: "Late payers", detail: "DSO 48 days", impact_annual: "12000.00" }, "not an object"],
  actions: [{ task_id: "SC-301", title: "Chase overdue invoices", owner_id: "grazyna" }, { title: "no id" }],
  sources: [{ name: "bank", status: "ok", count: 312, note: "" }, { name: "slack", status: "down", count: "x", note: "token expired" }],
  constraints: { no_hiring: true, max_new_monthly_cost: null },
};

test("forecast_plan evidence parses into numbers, sorted months and sanitised lists", () => {
  const plan = parseRevenuePlan([{ kind: "file", filename: "a.pdf" }, evidence], "SC-300");
  assert.ok(plan);
  assert.equal(plan.taskId, "SC-300");
  assert.equal(plan.targetAnnual, 450000);
  assert.equal(plan.realisticAnnual, 360000);
  assert.equal(plan.trailingRevenue, 300000);
  assert.equal(plan.targetSource, "default");
  assert.deepEqual(plan.months.map(month => month.month), ["2026-10-01", "2026-11-01", "2026-12-01"]);
  assert.equal(plan.months[2].forecast, 30000);
  assert.equal(plan.months[0].target, null);
  assert.equal(plan.weakPoints.length, 1);
  assert.equal(plan.weakPoints[0].impactAnnual, 12000);
  assert.deepEqual(plan.actions, [{ taskId: "SC-301", title: "Chase overdue invoices", ownerId: "grazyna" }]);
  assert.deepEqual(plan.sources.map(source => [source.name, source.status, source.count]), [["bank", "ok", 312], ["slack", "unavailable", 0]]);
  assert.deepEqual(plan.constraints, { noHiring: true, maxNewMonthlyCost: null });
});

test("missing or broken evidence gives null instead of throwing", () => {
  assert.equal(parseRevenuePlan(null), null);
  assert.equal(parseRevenuePlan(undefined), null);
  assert.equal(parseRevenuePlan([]), null);
  assert.equal(parseRevenuePlan([{ kind: "strategy_briefing" }]), null);
  assert.equal(parseRevenuePlan({ kind: "forecast_plan" }), null);
  assert.equal(parseRevenuePlan({ ...evidence, target_annual: "lots" }), null);
  const minimal = parseRevenuePlan({ kind: "forecast_plan", target_annual: "100", realistic_annual: "80" });
  assert.ok(minimal);
  assert.deepEqual([minimal.months, minimal.weakPoints, minimal.actions, minimal.sources], [[], [], [], []]);
  assert.equal(minimal.currency, "EUR");
});

test("gap sign: negative is short; a missing gap is derived as realistic minus target", () => {
  const short = parseRevenuePlan(evidence)!;
  assert.equal(short.gap, -90000);
  assert.equal(isShort(short), true);
  assert.equal(gapShare(short), -0.2);
  const derived = parseRevenuePlan({ ...evidence, gap: undefined, realistic_annual: "500000" })!;
  assert.equal(derived.gap, 50000);
  assert.equal(isShort(derived), false);
  assert.equal(gapShare({ gap: 10, targetAnnual: 0 }), 0);
});

test("money values and month labels", () => {
  assert.equal(moneyValue("1234.50"), 1234.5);
  assert.equal(moneyValue("-7"), -7);
  assert.equal(moneyValue("1e5"), null);
  assert.equal(moneyValue(""), null);
  assert.equal(moneyValue(null), null);
  assert.equal(moneyValue(Infinity), null);
  assert.equal(monthLabel("2026-12-01"), "Dec 26");
});

test("chart rows connect the forecast line to the last actual month", () => {
  const rows = revenueChartRows(parseRevenuePlan(evidence)!);
  assert.equal(rows.length, 3);
  assert.equal(rows[1].forecast, 26000, "last actual month starts the forecast line");
  assert.equal(rows[0].forecast, null);
  assert.equal(rows[2].label, "Dec 26");
});

test("drift alerts and open actions are picked from forecast-loop tasks only", () => {
  const task = (id: string, loop: string, category: string | null, status: string) => ({ id, loop, category, status, title: id, ownerId: null }) as Parameters<typeof driftAlerts>[0][number];
  const tasks = [
    task("a", "forecast", "drift", "escalated"), task("b", "forecast", "drift", "done"), task("c", "liquidity", "drift", "escalated"),
    task("d", "forecast", "forecast_action", "todo"), task("e", "forecast", "forecast_action", "done"),
  ];
  assert.deepEqual(driftAlerts(tasks).map(item => item.id), ["a"]);
  assert.deepEqual(openForecastActions(tasks).map(item => item.id), ["d"]);
});

test("settings input is validated before it reaches the backend", () => {
  const ok = parsePlanInput({ annual_revenue_target: "450 000,50", no_hiring: true, max_new_monthly_cost: "", guidance: "  grow catering  ", enabled: true });
  assert.deepEqual(ok, { ok: true, value: { annual_revenue_target: "450000.50", no_hiring: true, max_new_monthly_cost: null, guidance: "grow catering", enabled: true } });
  assert.equal(parsePlanInput({ annual_revenue_target: 450000, no_hiring: false, max_new_monthly_cost: 2000, guidance: "", enabled: false }).ok, true);
  for (const bad of [null, [], { annual_revenue_target: "0", no_hiring: false, enabled: true }, { annual_revenue_target: "-5", no_hiring: false, enabled: true },
    { annual_revenue_target: "abc", no_hiring: false, enabled: true }, { annual_revenue_target: "100", no_hiring: "yes", enabled: true },
    { annual_revenue_target: "100", no_hiring: false, enabled: true, max_new_monthly_cost: "lots" },
    { annual_revenue_target: "100", no_hiring: false, enabled: true, guidance: "x".repeat(2001) }]) {
    assert.equal(parsePlanInput(bad).ok, false, JSON.stringify(bad)?.slice(0, 80));
  }
});

test("backend plans and errors are reduced to safe shapes", () => {
  assert.deepEqual(sanitizePlan({ annual_revenue_target: "450000.00", no_hiring: true, max_new_monthly_cost: null, guidance: "g", enabled: true, updated_at: "2026-11-21T10:00:00Z", extra: "drop" }),
    { annual_revenue_target: "450000.00", no_hiring: true, max_new_monthly_cost: null, guidance: "g", enabled: true, updated_at: "2026-11-21T10:00:00Z" });
  assert.equal(sanitizePlan(null), null);
  assert.equal(sanitizePlan({ annual_revenue_target: "x" }), null);
  assert.deepEqual(backendError(403, null, "fallback"), { status: 403, error: "Only a workspace owner can change the forecast plan." });
  assert.deepEqual(backendError(422, { detail: "Target too large" }, "fallback"), { status: 422, error: "Target too large" });
  assert.deepEqual(backendError(400, { error: { message: "bad" } }, "fallback"), { status: 400, error: "bad" });
  assert.deepEqual(backendError(500, { detail: "Traceback: secret" }, "fallback"), { status: 502, error: "fallback" });
  assert.deepEqual(backendError(422, { detail: [{ loc: ["body"] }] }, "fallback"), { status: 422, error: "fallback" });
});
