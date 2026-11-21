// JSON adapters to the unchanged finance/invoice source copies.
import * as finance from "./finance-model.ts";
import { parseTransactionInvoice } from "./invoice-model.ts";

function input(text: string): unknown {
  try { return JSON.parse(text); }
  catch { throw new Error("Invalid JSON input"); }
}

function nullable(text: string): string | null {
  const value = input(text);
  if (value !== null && typeof value !== "string") throw new Error("Expected string or null");
  return value;
}

type PeriodRow = { category: string | null; count: string; income: string; expense: string };
function periodRows(value: unknown): PeriodRow[] {
  if (!Array.isArray(value) || value.some(row => !row || typeof row !== "object"
    || (row.category !== null && typeof row.category !== "string")
    || ![row.count, row.income, row.expense].every(v => typeof v === "string"))) throw new Error("Invalid finance rows");
  return value as PeriodRow[];
}

type SpendRow = { id: string | null; name: string; budget: string | null; spent: string };
function spendRows(value: unknown): SpendRow[] {
  if (!Array.isArray(value) || value.some(row => !row || typeof row !== "object"
    || (row.id !== null && typeof row.id !== "string") || typeof row.name !== "string"
    || (row.budget !== null && typeof row.budget !== "string") || typeof row.spent !== "string")) throw new Error("Invalid spend rows");
  return value as SpendRow[];
}

export function checkDisplayCents(cents: string): number {
  return finance.displayCents(cents);
}

export function checkDecimalCents(amount: string): string {
  return finance.decimalCents(amount).toString();
}

export function checkFinancePeriod(rows: string): string {
  return JSON.stringify(finance.financePeriod(periodRows(input(rows))));
}

export function checkQuarterSpend(start: string, through: string, rows: string): string {
  return JSON.stringify(finance.quarterSpend(nullable(start), nullable(through), spendRows(input(rows))));
}

export function checkInvoice(value: string): string {
  return JSON.stringify(parseTransactionInvoice(input(value)));
}
