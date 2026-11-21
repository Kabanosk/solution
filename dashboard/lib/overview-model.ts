// Pure logic behind the Overview: what-if scenario, safety-buffer gauge, team donut and the "What Scout did" ring.
import type { ForecastPoint, TeamSpend } from "./finance-model";
import type { AgentTask } from "./scout-model";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** A liquidity what-if ("What if: new oven €30,000.00 in March 2027"): an outflow on the 1st of that month. */
export type WhatIf = { taskId: string; label: string; what: string; amount: number; on: string };

/** The latest liquidity what-if with an amount and a month, as the liquidity loop titles them. Cancelled ones are skipped. */
export function latestWhatIf(tasks: AgentTask[]): WhatIf | null {
  const candidates = tasks
    .filter(task => task.loop === "liquidity" && task.status !== "cancelled" && task.amount !== null && task.amount !== 0)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  for (const task of candidates) {
    const match = /^What if:\s*(.+?)\s+in\s+([A-Za-z]+)\s+(\d{4})\s*$/.exec(task.title);
    const month = match ? MONTHS.indexOf(match[2][0].toUpperCase() + match[2].slice(1).toLowerCase()) : -1;
    if (!match || month < 0) continue;
    const what = match[1].replace(/\s*€[\d,.]+$/, "").trim() || "the purchase";
    return { taskId: task.id, label: task.title.replace(/^What if:\s*/, ""), what, amount: Math.abs(task.amount!), on: `${match[3]}-${String(month + 1).padStart(2, "0")}-01` };
  }
  return null;
}

/** The forecast with the what-if outflow taken off every point from its date on (as the liquidity loop models it). */
export function withWhatIf(points: ForecastPoint[], whatIf: WhatIf | null): ForecastPoint[] {
  if (!whatIf) return points;
  return points.map(point => point.date >= whatIf.on ? { ...point, balance: Math.round((point.balance - whatIf.amount) * 100) / 100 } : point);
}

/** 4–6 round ticks (1, 2, 2.5 or 5 × 10ⁿ) that cover [low, high] with a little room. */
export function niceTicks(low: number, high: number): number[] {
  const span = Math.max(high - low, 1);
  const raw = span / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map(f => f * power).find(candidate => candidate >= raw)!;
  const start = Math.floor((low - span * 0.04) / step) * step;
  const end = Math.ceil((high + span * 0.04) / step) * step;
  return Array.from({ length: Math.round((end - start) / step) + 1 }, (_, index) => start + index * step);
}

export type BufferStatus = "healthy" | "tight" | "below";
export const BUFFER_STATUS_LABEL: Record<BufferStatus, string> = { healthy: "Healthy", tight: "Tight", below: "Below buffer" };
/** Below 1× the buffer is below; under 1.5× is tight (one bad month away); otherwise healthy. */
export const TIGHT_UNDER = 1.5;

export function coverage(balance: number, buffer: number): number {
  return buffer > 0 ? balance / buffer : Infinity;
}

export function bufferStatus(ratio: number): BufferStatus {
  return ratio < 1 ? "below" : ratio < TIGHT_UNDER ? "tight" : "healthy";
}

/** Worst of today and the lowest point ahead: a dip below the buffer next month matters even if today is fine. */
export function gaugeStatus(balance: number, lowest: number, buffer: number): BufferStatus {
  const order: BufferStatus[] = ["below", "tight", "healthy"];
  const today = bufferStatus(coverage(balance, buffer));
  const ahead = bufferStatus(coverage(lowest, buffer));
  return order[Math.min(order.indexOf(today), order.indexOf(ahead))];
}

/** "1.2×", "12×" */
export function formatCoverage(ratio: number): string {
  if (!Number.isFinite(ratio)) return "—";
  return `${ratio >= 10 ? ratio.toFixed(0) : ratio.toFixed(1)}×`;
}

export type DonutSlice = TeamSpend & { share: number; budgetShare: number | null; over: boolean };

/** Teams with spend become slices (shares of the total); budget share is spent / quarter budget. */
export function donutSlices(teams: TeamSpend[]): { slices: DonutSlice[]; total: number } {
  const total = Math.round(teams.reduce((sum, team) => sum + team.spent, 0) * 100) / 100;
  const slices = teams.map(team => ({
    ...team,
    share: total > 0 ? team.spent / total : 0,
    budgetShare: team.budget ? team.spent / team.budget : null,
    over: team.budget !== null && team.spent > team.budget,
  }));
  return { slices, total };
}

export type ScoutOutcome = "alone" | "told" | "asked";
export const SCOUT_OUTCOME_LABEL: Record<ScoutOutcome, string> = { alone: "Did it alone", told: "Did it and told you", asked: "Asked you first" };

/** How a task ended, or null while it is still running (or was cancelled without anyone being asked). */
export function scoutOutcome(task: AgentTask): ScoutOutcome | null {
  if (task.status === "escalated" || task.resolvedVia === "approval" || task.resolvedVia === "slack_dm" || task.level === "APPROVAL") {
    return task.status === "escalated" || task.status === "done" || task.status === "cancelled" ? "asked" : null;
  }
  if (task.status !== "done") return null;
  return task.level === "NOTIFY" ? "told" : "alone";
}

/** Tasks opened this month (UTC) split by how they ended. Chat answers are not actions and stay out. */
export function scoutRing(tasks: AgentTask[], now: string) {
  const month = now.slice(0, 7);
  const counts: Record<ScoutOutcome, number> = { alone: 0, told: 0, asked: 0 };
  let running = 0;
  for (const task of tasks) {
    if (task.loop === "chat" || task.createdAt.slice(0, 7) !== month) continue;
    const outcome = scoutOutcome(task);
    if (outcome) counts[outcome] += 1;
    else if (task.status !== "cancelled") running += 1;
  }
  return { counts, total: counts.alone + counts.told + counts.asked, running };
}

/** The latest actions worth a line: ended, non-chat tasks with an outcome, newest first. */
export function notableActions(tasks: AgentTask[], limit = 3): (AgentTask & { how: ScoutOutcome })[] {
  return tasks
    .filter(task => task.loop !== "chat" && task.outcome && scoutOutcome(task))
    .map(task => ({ ...task, how: scoutOutcome(task)! }))
    .sort((a, b) => (b.closedAt ?? b.updatedAt).localeCompare(a.closedAt ?? a.updatedAt))
    .slice(0, limit);
}

/** A short line for the timeline from a task's raw outcome: no status prefix, no parentheticals, whole euros. */
export function actionSummary(task: Pick<AgentTask, "outcome" | "title" | "simulated" | "status">): string {
  const raw = (task.outcome ?? task.title).replace(/^(Resolved|Closed|Done)\s*·\s*/i, "");
  const simulated = task.simulated || /\(SIMULATED\b/i.test(raw);
  let text = raw.replace(/\s*\([^)]*\)?/g, "").replace(/€([\d,]+)\.00\b/g, "€$1").trim();
  const credit = /^Credit line of (€[\d,]+(?:\.\d\d)?)/.exec(text);
  const filed = /^(€[\d,]+(?:\.\d\d)?) filed as (\S+)/.exec(text);
  if (credit) text = `Credit line ${credit[1]} ${task.status === "done" ? "approved" : "proposed"}`;
  else if (filed) text = `Invoice ${filed[2]} filed · ${filed[1]}`;
  else text = text.split(/(?<=[.:;])\s/)[0].replace(/[.:;]$/, "");
  if (text.length > 72) text = `${text.slice(0, 70).trimEnd()}…`;
  return simulated ? `${text} (simulated)` : text;
}
