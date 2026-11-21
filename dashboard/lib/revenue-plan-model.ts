// Forecast loop: the CEO's annual revenue target and Scout's realistic 12-month forecast.
// Pure helpers shared by the server query, the overview chart, the task detail, the route handlers and tests.
// Money arrives as decimal strings; it is converted to numbers only for display and charts.
import type { AgentTask } from "./scout-model";

export type RevenuePlanMonth = { month: string; actual: number | null; target: number | null; forecast: number | null };
export type RevenueWeakPoint = { area: string; detail: string; impactAnnual: number | null };
export type RevenueAction = { taskId: string; title: string; ownerId: string | null };
export type RevenueSource = { name: string; status: "ok" | "unavailable"; count: number; note: string };
export type RevenuePlan = {
  /** The forecast_review task the result was read from. */
  taskId: string | null;
  asOf: string;
  currency: string;
  trailingRevenue: number | null;
  targetAnnual: number;
  targetSource: "default" | "ceo";
  realisticAnnual: number;
  /** realistic − target: negative means the forecast falls short of the target. */
  gap: number;
  months: RevenuePlanMonth[];
  weakPoints: RevenueWeakPoint[];
  actions: RevenueAction[];
  sources: RevenueSource[];
  constraints: { noHiring: boolean; maxNewMonthlyCost: number | null };
};

type Rec = Record<string, unknown>;
const isRecord = (value: unknown): value is Rec => typeof value === "object" && value !== null && !Array.isArray(value);
const DECIMAL = /^-?\d+(\.\d+)?$/;
const text = (value: unknown, max = 500) => typeof value === "string" ? value.trim().slice(0, max) : "";

/** "1234.50" / 1234.5 to a finite number; anything else to null. */
export function moneyValue(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!DECIMAL.test(trimmed)) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

const list = (value: unknown): Rec[] => Array.isArray(value) ? value.filter(isRecord) : [];

/** The forecast_plan evidence item (or an evidence array that contains one) to a plan; null when it is missing or broken. */
export function parseRevenuePlan(evidence: unknown, taskId: string | null = null): RevenuePlan | null {
  const item = Array.isArray(evidence) ? evidence.find(entry => isRecord(entry) && entry.kind === "forecast_plan") : evidence;
  if (!isRecord(item) || item.kind !== "forecast_plan") return null;
  const targetAnnual = moneyValue(item.target_annual);
  const realisticAnnual = moneyValue(item.realistic_annual);
  if (targetAnnual === null || realisticAnnual === null) return null;
  const gap = moneyValue(item.gap) ?? realisticAnnual - targetAnnual;
  const constraints = isRecord(item.constraints) ? item.constraints : {};
  return {
    taskId, asOf: text(item.as_of, 10), currency: text(item.currency, 3) || "EUR",
    trailingRevenue: moneyValue(item.trailing_revenue), targetAnnual, realisticAnnual, gap,
    targetSource: item.target_source === "ceo" ? "ceo" : "default",
    months: list(item.months).filter(month => /^\d{4}-\d{2}/.test(text(month.month))).slice(0, 36).map(month => ({
      month: text(month.month, 10), actual: moneyValue(month.actual), target: moneyValue(month.target), forecast: moneyValue(month.forecast),
    })).sort((a, b) => a.month.localeCompare(b.month)),
    weakPoints: list(item.weak_points).slice(0, 20).map(point => ({ area: text(point.area, 120) || "Weak point", detail: text(point.detail, 1000), impactAnnual: moneyValue(point.impact_annual) })),
    actions: list(item.actions).filter(action => text(action.task_id)).slice(0, 30).map(action => ({
      taskId: text(action.task_id, 40), title: text(action.title, 300) || text(action.task_id, 40), ownerId: text(action.owner_id, 80) || null,
    })),
    sources: list(item.sources).slice(0, 10).map(source => ({
      name: text(source.name, 40) || "source", status: source.status === "ok" ? "ok" : "unavailable",
      count: typeof source.count === "number" && Number.isFinite(source.count) ? source.count : 0, note: text(source.note, 300),
    })),
    constraints: { noHiring: constraints.no_hiring === true, maxNewMonthlyCost: moneyValue(constraints.max_new_monthly_cost) },
  };
}

/** True when the realistic forecast falls short of the target. */
export const isShort = (plan: Pick<RevenuePlan, "gap">) => plan.gap < 0;

/** Gap as a share of the target: −0.2 is 20 % short. */
export const gapShare = (plan: Pick<RevenuePlan, "gap" | "targetAnnual">) => plan.targetAnnual > 0 ? plan.gap / plan.targetAnnual : 0;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "2026-11-01" to "Nov 26". */
export function monthLabel(month: string): string {
  const [year, number] = month.split("-").map(Number);
  return `${MONTHS[(number || 1) - 1] ?? "?"} ${String(year).slice(-2)}`;
}

/** Chart rows: past months carry actuals, future months the target path and Scout's forecast. The last actual month
 *  also starts the forecast line so the two connect. */
export function revenueChartRows(plan: Pick<RevenuePlan, "months">) {
  const rows = plan.months.map(month => ({ month: month.month, label: monthLabel(month.month), actual: month.actual, target: month.target, forecast: month.forecast }));
  const firstForecast = rows.findIndex(row => row.forecast !== null);
  if (firstForecast > 0) {
    const previous = rows[firstForecast - 1];
    if (previous.actual !== null && previous.forecast === null) rows[firstForecast - 1] = { ...previous, forecast: previous.actual };
  }
  return rows;
}

type TaskLike = Pick<AgentTask, "id" | "loop" | "category" | "status" | "title" | "ownerId">;
/** Open drift alerts: plan contradictions escalated to the CEO. */
export const driftAlerts = <T extends TaskLike>(tasks: T[]) => tasks.filter(task => task.loop === "forecast" && task.category === "drift" && task.status === "escalated");
/** Open optimisation tasks the forecast review created. */
export const openForecastActions = <T extends TaskLike>(tasks: T[]) => tasks.filter(task => task.loop === "forecast" && task.category === "forecast_action" && !["done", "cancelled"].includes(task.status));

// ---- Settings (PUT /api/forecast/plan) ----

export type ForecastPlanSettings = {
  annual_revenue_target: string; no_hiring: boolean; max_new_monthly_cost: string | null; guidance: string; enabled: boolean; updated_at: string;
};
export type ForecastPlanResponse = { plan: ForecastPlanSettings | null; defaults: { trailing_revenue: string; default_target: string; currency: string } };
export type ForecastPlanInput = { annual_revenue_target: string; no_hiring: boolean; max_new_monthly_cost: string | null; guidance: string; enabled: boolean };

const MAX_GUIDANCE = 2000; // stays under the 8 KB request-body limit with multi-byte text
const MONEY_INPUT = /^\d{1,12}(\.\d{1,2})?$/;
/** "150 000,50" / "150000.5" / 150000 to "150000.50"-style decimal string, or null. */
function moneyInput(value: unknown): string | null {
  const raw = typeof value === "number" ? (Number.isFinite(value) ? String(value) : "") : typeof value === "string" ? value : "";
  const cleaned = raw.replace(/[\s€_]/g, "").replace(",", ".");
  return MONEY_INPUT.test(cleaned) ? cleaned : null;
}

/** Validates the browser's settings body before it is forwarded to the backend. */
export function parsePlanInput(raw: unknown): { ok: true; value: ForecastPlanInput } | { ok: false; error: string } {
  if (!isRecord(raw)) return { ok: false, error: "Invalid request." };
  const target = moneyInput(raw.annual_revenue_target);
  if (target === null || Number(target) <= 0) return { ok: false, error: "Enter an annual revenue target in euros, e.g. 450000." };
  let maxCost: string | null = null;
  if (raw.max_new_monthly_cost !== null && raw.max_new_monthly_cost !== undefined && String(raw.max_new_monthly_cost).trim() !== "") {
    maxCost = moneyInput(raw.max_new_monthly_cost);
    if (maxCost === null) return { ok: false, error: "Max new monthly cost must be an amount in euros, or empty." };
  }
  if (raw.guidance !== undefined && typeof raw.guidance !== "string") return { ok: false, error: "Guidance must be text." };
  const guidance = (raw.guidance ?? "") as string;
  if (guidance.length > MAX_GUIDANCE) return { ok: false, error: `Guidance is limited to ${MAX_GUIDANCE} characters.` };
  if (typeof raw.no_hiring !== "boolean" || typeof raw.enabled !== "boolean") return { ok: false, error: "Invalid request." };
  return { ok: true, value: { annual_revenue_target: target, no_hiring: raw.no_hiring, max_new_monthly_cost: maxCost, guidance: guidance.trim(), enabled: raw.enabled } };
}

/** A backend plan object reduced to the known fields; null when it is not one. */
export function sanitizePlan(value: unknown): ForecastPlanSettings | null {
  if (!isRecord(value)) return null;
  const target = moneyValue(value.annual_revenue_target);
  if (target === null) return null;
  return {
    annual_revenue_target: String(value.annual_revenue_target), no_hiring: value.no_hiring === true,
    max_new_monthly_cost: moneyValue(value.max_new_monthly_cost) === null ? null : String(value.max_new_monthly_cost),
    guidance: text(value.guidance, MAX_GUIDANCE), enabled: value.enabled === true, updated_at: text(value.updated_at, 40),
  };
}

/** Safe error text from a backend error body ({detail}, {error}, {error:{message}}). Never echoes long or structured bodies. */
export function backendError(status: number, data: unknown, fallback: string): { status: number; error: string } {
  if (status >= 500 || status < 400) return { status: 502, error: fallback };
  let message = "";
  if (isRecord(data)) {
    const candidate = data.detail ?? data.error ?? data.message;
    if (typeof candidate === "string") message = candidate;
    else if (isRecord(candidate) && typeof candidate.message === "string") message = candidate.message;
  }
  if (status === 403) message ||= "Only a workspace owner can change the forecast plan.";
  message = message.replace(/[\u0000-\u001f]/g, " ").trim().slice(0, 300);
  return { status, error: message || fallback };
}
