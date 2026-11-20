import type { AgentJob } from "./agent-jobs.ts";

export type ExpenseCategory = { name: string; color: string; amount: number; percentage: number };
export type FinancePeriod = { transactions: number; totalRevenue: number; totalExpenses: number; expenses: ExpenseCategory[] };
export type Employee = { id: string; name: string; initials: string; role: string; team: string; salary: number | null; avatar: string };
export type Decision = { id: string; ts: string; summary: string; reason: string; level: string; status: string; executed_simulated: boolean };
export type Forecast = { asOf: string; model: string; safetyBuffer: number; points: { day: number; balance: number }[] };
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
  pendingApprovals: number;
  decisions: Decision[];
  jobs: AgentJob[];
  forecast: Forecast | null;
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

const palette: Record<string, [string, string]> = {
  payroll: ["Payroll", "#277757"], food: ["Food", "#929292"],
  beverages: ["Beverages", "#626262"], partner_investment: ["Partner investment", "#F3A455"],
  rent: ["Rent", "#42C994"], utilities: ["Utilities", "#277757"],
  tax: ["Tax", "#E86B6B"], social_security: ["Social security", "#F3A455"],
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
      color: palette[row.category ?? ""]?.[1] ?? "#626262",
      amount: displayCents(row.expense),
      percentage: expense === 0n ? 0 : Number(BigInt(row.expense) * 10000n / expense) / 100,
    })),
  };
}
