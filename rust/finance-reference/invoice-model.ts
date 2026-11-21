// The invoice behind a documented bank booking (Scout API GET /v1/transactions/{id}/invoice). Pure, no server imports.
import { decimalCents, displayCents } from "./finance-model.ts";

export type InvoiceLine = { position: number; description: string; quantity: number; unitPrice: number; amount: number; category: string | null };
export type TransactionInvoice = {
  transaction: { id: string; bookingDate: string; reference: string; counterparty: string; amount: number; currency: string };
  invoice: { id: string; number: string; supplier: string; issueDate: string; dueDate: string | null; total: number; currency: string; source: string; category: string | null };
  lines: InvoiceLine[];
  document: { taskId: string; filedAt: string | null; source: string | null; textExcerpt: string | null; evidence: Record<string, unknown> | null } | null;
  taskId: string | null;
};

type Row = Record<string, unknown>;
const object = (value: unknown): Row => {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid invoice");
  return value as Row;
};
const text = (value: unknown): string => { if (typeof value !== "string") throw new Error("Invalid invoice text"); return value; };
const optional = (value: unknown): string | null => value === null || value === undefined ? null : text(value);
/** Money arrives as a decimal string ("-120.00"); keep cent precision through BigInt. */
const money = (value: unknown): number => {
  const raw = text(value);
  const match = /^(-?)(\d+)(?:\.(\d{1,4}))?$/.exec(raw);
  if (!match) throw new Error("Invalid invoice amount");
  const cents = (match[3] ?? "").padEnd(2, "0").slice(0, 2);
  return displayCents(decimalCents(`${match[1]}${match[2]}.${cents}`));
};
const decimal = (value: unknown): number => { const result = Number(text(value)); if (!Number.isFinite(result)) throw new Error("Invalid number"); return result; };

export function parseTransactionInvoice(value: unknown): TransactionInvoice {
  const body = object(value);
  const txn = object(body.transaction);
  const inv = object(body.invoice);
  if (!Array.isArray(body.lines)) throw new Error("Invalid invoice lines");
  const doc = body.document === null || body.document === undefined ? null : object(body.document);
  return {
    transaction: { id: text(txn.id), bookingDate: text(txn.booking_date), reference: text(txn.reference), counterparty: text(txn.counterparty),
      amount: money(txn.amount), currency: optional(txn.currency) ?? "EUR" },
    invoice: { id: text(inv.id), number: text(inv.number), supplier: text(inv.supplier), issueDate: text(inv.issue_date), dueDate: optional(inv.due_date),
      total: money(inv.total), currency: optional(inv.currency) ?? "EUR", source: optional(inv.source) ?? "db", category: optional(inv.category) },
    lines: body.lines.map(object).map(line => ({ position: Number(line.position), description: text(line.description), quantity: decimal(line.quantity),
      unitPrice: decimal(line.unit_price), amount: money(line.amount), category: optional(line.category) })),
    document: doc ? { taskId: text(doc.task_id), filedAt: optional(doc.filed_at), source: optional(doc.source),
      textExcerpt: optional(doc.text_excerpt)?.slice(0, 4000) ?? null,
      evidence: doc.evidence && typeof doc.evidence === "object" && !Array.isArray(doc.evidence) ? doc.evidence as Record<string, unknown> : null } : null,
    taskId: optional(body.task_id),
  };
}
