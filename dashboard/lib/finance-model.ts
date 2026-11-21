import type { ScoutData } from "./scout-model";

export type ExpenseCategory = { name: string; color: string; amount: number; percentage: number };
export type FinancePeriod = { transactions: number; totalRevenue: number; totalExpenses: number; expenses: ExpenseCategory[] };
export type Employee = { id: string; name: string; initials: string; role: string; team: string; salary: number | null; avatar: string };
export type ForecastPoint = { day: number; date: string; balance: number };
/** One forecast run of the CFO API: `day` counts days from `asOf`; `lowest` covers the whole horizon. */
export type Forecast = {
  asOf: string; model: string; horizonDays: number; openingBalance: number; safetyBuffer: number;
  lowest: { date: string; balance: number }; points: ForecastPoint[];
};
/** Spend per team in the current quarter. `budget` is three monthly budgets; null for spend without a team. */
export type TeamSpend = { id: string | null; name: string; spent: number; budget: number | null };
export type QuarterSpend = { label: string; start: string; through: string; teams: TeamSpend[] };
export type DashboardData = {
  asOf: string;
  yearLabel: string;
  year: FinancePeriod;
  q4: FinancePeriod;
  employees: Employee[];
  monthlyPayroll: number;
  monthlyContributions: number;
  closingBalance: number;
  undocumentedPayments: number;
  scout: ScoutData;
  /** 90 days, daily points. */
  forecast: Forecast | null;
  /** 12 months, weekly points. */
  forecastYear: Forecast | null;
  quarterSpend: QuarterSpend;
  /** Server time of this render (ISO), so "this month" is the same on the server and in the browser. */
  generatedAt: string;
};

// Perform money arithmetic in Postgres NUMERIC/integer cents. Convert final
// aggregates to JS numbers only for display and Recharts.
export function displayCents(cents: string | bigint): number {
  const value = BigInt(cents);
  if (value > BigInt(Number.MAX_SAFE_INTEGER) || value < BigInt(Number.MIN_SAFE_INTEGER)) {
    throw new Error("Money aggregate exceeds display precision");
  }
  return Number(value) / 100;
}

export function decimalCents(amount: string): bigint {
  const match = /^(-?)(\d+)\.(\d{2})$/.exec(amount);
  if (!match) throw new Error("Invalid money amount");
  return (BigInt(match[2]) * 100n + BigInt(match[3])) * (match[1] ? -1n : 1n);
}

// the same teal ramp + greys as the Overview charts (globals.css --chart-*): no one-off hex colours
const palette: Record<string, [string, string]> = {
  payroll: ["Payroll", "var(--chart-balance)"], social_security: ["Social security", "var(--chart-2)"],
  rent: ["Rent", "var(--chart-3)"], tax: ["Tax", "var(--chart-4)"], utilities: ["Utilities", "var(--chart-5)"],
  food: ["Food", "var(--chart-2)"], beverages: ["Beverages", "var(--chart-3)"],
  partner_investment: ["Partner investment", "var(--chart-5)"],
};

export function financePeriod(rows: { category: string | null; count: string; income: string; expense: string }[]): FinancePeriod {
  let income = 0n;
  let expense = 0n;
  let transactions = 0;
  for (const row of rows) {
    income += BigInt(row.income);
    expense += BigInt(row.expense);
    transactions += Number(row.count);
  }
  return {
    transactions, totalRevenue: displayCents(income), totalExpenses: displayCents(expense),
    expenses: rows.filter(row => BigInt(row.expense) > 0n).map(row => ({
      name: palette[row.category ?? ""]?.[0] ?? row.category?.replaceAll("_", " ") ?? "Other",
      color: palette[row.category ?? ""]?.[1] ?? "var(--chart-4)",
      amount: displayCents(row.expense),
      percentage: expense === 0n ? 0 : Number(BigInt(row.expense) * 10000n / expense) / 100,
    })),
  };
}

/** Rows of the quarter-spend query (integer cents as text) to display amounts; biggest spender first, empty teams last. */
export function quarterSpend(start: string | null, through: string | null,
  rows: { id: string | null; name: string; budget: string | null; spent: string }[]): QuarterSpend {
  const begin = start ?? "1970-01-01";
  const label = `Q${Math.floor(Number(begin.slice(5, 7)) / 3) + 1} ${begin.slice(0, 4)}`;
  const teams = rows.map(row => ({ id: row.id, name: row.name, spent: displayCents(row.spent), budget: row.budget === null ? null : displayCents(row.budget) }))
    .filter(team => team.id !== null || team.spent > 0)
    .sort((a, b) => b.spent - a.spent || a.name.localeCompare(b.name));
  return { label, start: begin, through: through ?? begin, teams };
}
