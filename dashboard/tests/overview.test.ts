import { test } from "node:test";
import assert from "node:assert/strict";
import { quarterSpend } from "../lib/finance-model.ts";
import { actionSummary, donutSlices, formatCoverage, gaugeStatus, latestWhatIf, niceTicks, notableActions, scoutOutcome, scoutRing, withWhatIf } from "../lib/overview-model.ts";
import type { AgentTask } from "../lib/scout-model.ts";

const task = (patch: Partial<AgentTask>): AgentTask => ({
  id: "SC-1", loop: "invoicing", title: "Task", status: "done", ownerId: null, requestedBy: "agent", resolvedVia: null, outcome: null,
  level: "AUTO", simulated: false, category: null, amount: null, currency: "EUR", counterparty: null, transactionId: null, evidence: [],
  pendingApprovalFor: null, createdAt: "2026-11-20T10:00:00Z", updatedAt: "2026-11-20T10:00:00Z", closedAt: null, ...patch,
});

test("latest what-if: newest liquidity task with a month and an amount, cancelled ones skipped", () => {
  const tasks = [
    task({ id: "SC-245", loop: "liquidity", title: "What if: new oven €30,000.00 in March 2027", amount: -30000, createdAt: "2026-11-21T00:10:02Z" }),
    task({ id: "SC-250", loop: "liquidity", title: "What if: purchase €46,000.00 in May 2027", amount: -46000, status: "cancelled", createdAt: "2026-11-21T01:00:00Z" }),
    task({ id: "SC-251", loop: "liquidity", title: "Review December cash forecast", amount: null, createdAt: "2026-11-21T02:00:00Z" }),
    task({ id: "SC-252", loop: "invoicing", title: "What if: x €1.00 in June 2027", amount: -1, createdAt: "2026-11-21T03:00:00Z" }),
  ];
  assert.deepEqual(latestWhatIf(tasks), { taskId: "SC-245", label: "new oven €30,000.00 in March 2027", what: "new oven", amount: 30000, on: "2027-03-01" });
  assert.equal(latestWhatIf([]), null);
  assert.equal(latestWhatIf([task({ loop: "liquidity", title: "What if: oven €5.00 in Smarch 2027", amount: -5 })]), null);
});

test("what-if scenario takes the outflow off every point from its month on", () => {
  const points = [{ day: 0, date: "2027-02-27", balance: 50000 }, { day: 7, date: "2027-03-01", balance: 51000 }, { day: 14, date: "2027-03-08", balance: 52000.5 }];
  const scenario = withWhatIf(points, { taskId: "SC-1", label: "", what: "oven", amount: 30000, on: "2027-03-01" });
  assert.deepEqual(scenario.map(point => point.balance), [50000, 21000, 22000.5]);
  assert.equal(withWhatIf(points, null), points);
});

test("gauge status is the worse of today and the lowest point ahead", () => {
  assert.equal(gaugeStatus(40000, 30000, 18000), "healthy");
  assert.equal(gaugeStatus(22050, 22058, 17955), "tight");
  assert.equal(gaugeStatus(40000, 12000, 18000), "below");
  assert.equal(gaugeStatus(10000, 40000, 18000), "below");
  assert.equal(formatCoverage(22050.31 / 17955), "1.2×");
  assert.equal(formatCoverage(12.4), "12×");
  assert.equal(formatCoverage(Infinity), "—");
});

test("donut slices: shares sum to the total, budget pill is over only above budget", () => {
  const { slices, total } = donutSlices([
    { id: "kitchen", name: "Kitchen", spent: 57985.07, budget: 85200 },
    { id: null, name: "Unassigned", spent: 15888.34, budget: null },
    { id: "it", name: "IT", spent: 900, budget: 750 },
    { id: "marketing", name: "Marketing", spent: 0, budget: 2550 },
  ]);
  assert.equal(total, 74773.41);
  assert.ok(Math.abs(slices.reduce((sum, slice) => sum + slice.share, 0) - 1) < 1e-9);
  assert.deepEqual(slices.map(slice => slice.over), [false, false, true, false]);
  assert.equal(slices[1].budgetShare, null);
  assert.equal(slices[3].budgetShare, 0);
});

test("quarter spend rows: label, cents, empty no-team row dropped, biggest first", () => {
  const spend = quarterSpend("2026-10-01", "2026-11-20", [
    { id: "front", name: "Front", budget: "2835000", spent: "1275000" },
    { id: "kitchen", name: "Kitchen", budget: "8520000", spent: "5798507" },
    { id: "it", name: "IT", budget: "75000", spent: "0" },
    { id: null, name: "Unassigned", budget: null, spent: "0" },
  ]);
  assert.equal(spend.label, "Q4 2026");
  assert.deepEqual(spend.teams.map(team => [team.id, team.spent, team.budget]), [["kitchen", 57985.07, 85200], ["front", 12750, 28350], ["it", 0, 750]]);
  assert.equal(quarterSpend("2027-01-01", "2027-02-03", []).label, "Q1 2027");
});

test("what Scout did: alone / told / asked, this month only, chat and running tasks out", () => {
  assert.equal(scoutOutcome(task({ resolvedVia: "files" })), "alone");
  assert.equal(scoutOutcome(task({ resolvedVia: "auto", level: "NOTIFY" })), "told");
  assert.equal(scoutOutcome(task({ resolvedVia: "approval", level: "APPROVAL" })), "asked");
  assert.equal(scoutOutcome(task({ resolvedVia: "approval", status: "cancelled" })), "asked");
  assert.equal(scoutOutcome(task({ status: "escalated", resolvedVia: null, level: null })), "asked");
  assert.equal(scoutOutcome(task({ status: "waiting", level: "NOTIFY" })), null);
  assert.equal(scoutOutcome(task({ status: "cancelled" })), null);
  const ring = scoutRing([
    task({ resolvedVia: "email" }), task({ resolvedVia: "files" }), task({ resolvedVia: "auto", level: "NOTIFY" }),
    task({ resolvedVia: "approval", level: "APPROVAL" }), task({ status: "escalated", level: null }),
    task({ status: "waiting", level: "NOTIFY" }), task({ loop: "chat", resolvedVia: "auto" }),
    task({ resolvedVia: "files", createdAt: "2026-10-31T23:59:59Z" }),
  ], "2026-11-21T09:00:00Z");
  assert.deepEqual(ring, { counts: { alone: 2, told: 1, asked: 2 }, total: 5, running: 1 });
});

test("notable actions: latest three ended non-chat tasks with an outcome", () => {
  const actions = notableActions([
    task({ id: "A", outcome: "Resolved", closedAt: "2026-11-20T08:00:00Z" }),
    task({ id: "B", outcome: "Resolved", closedAt: "2026-11-21T08:00:00Z", level: "NOTIFY" }),
    task({ id: "C", outcome: null, closedAt: "2026-11-21T09:00:00Z" }),
    task({ id: "D", loop: "chat", outcome: "Answered", closedAt: "2026-11-21T10:00:00Z" }),
    task({ id: "E", outcome: "Approved", resolvedVia: "approval", closedAt: "2026-11-19T08:00:00Z" }),
    task({ id: "F", outcome: "Old", closedAt: "2026-11-01T08:00:00Z" }),
  ]);
  assert.deepEqual(actions.map(action => [action.id, action.how]), [["B", "told"], ["A", "alone"], ["E", "asked"]]);
});

test("forecast axis ticks are round and cover the data", () => {
  assert.deepEqual(niceTicks(17955, 41000), [10000, 20000, 30000, 40000, 50000]);
  assert.deepEqual(niceTicks(-473248, 118000), [-600000, -400000, -200000, 0, 200000]);
});

test("timeline summaries are short and readable", () => {
  const summary = (outcome: string, patch: Partial<AgentTask> = {}) => actionSummary(task({ outcome, ...patch }));
  assert.equal(summary("Resolved · Credit line of €18,000.00 for Feb 15–Jul 21 (SIMULATED: no money moved, the bank request is prepared for Wojciech Fiołka)", { simulated: true }),
    "Credit line €18,000 approved (simulated)");
  assert.equal(summary("Resolved · €642.80 filed as CWM-4471"), "Invoice CWM-4471 filed · €642.80");
  assert.equal(summary("Closed · Wojciech Fiołka rejected the proposal", { status: "cancelled" }), "Wojciech Fiołka rejected the proposal");
  const long = summary("Cloud cost for the ordering app rose 50.0% month over month (from EUR 244.01 to EUR 366.02) while online orders grew only 2.9%: Log ingestion went up.");
  assert.ok(!long.includes("(") && long.length <= 72 && long.startsWith("Cloud cost for the ordering app rose 50.0% month over month while"), long);
});
