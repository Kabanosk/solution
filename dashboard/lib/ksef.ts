// Defensive parsing of the KSeF connection config (jsonb written by the backend; never trusted).
export type KsefPhase = "idle" | "running" | "done" | "error";
export type KsefStep = { at: string | null; text: string; ok: boolean };
export type KsefInvoice = { number: string; ksefNumber: string; supplier: string; total: string; currency: string; linked: boolean };
export type KsefConfig = {
  env: string | null; api: string | null; nip: string | null; company: string | null; phase: KsefPhase;
  steps: KsefStep[]; issued: number; received: number; imported: number; linked: number; linkedTotal: string | null;
  onFile: number; unpaid: number; unpaidTotal: string | null;
  invoices: KsefInvoice[];
};

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null => typeof value === "string" && value !== "" ? value : null;
const count = (value: unknown): number => typeof value === "number" && Number.isFinite(value) ? value : 0;
const PHASES: readonly string[] = ["idle", "running", "done", "error"];
const isPhase = (value: unknown): value is KsefPhase => typeof value === "string" && PHASES.includes(value);

function step(value: unknown): KsefStep | null {
  if (!isRecord(value)) return null;
  const label = text(value.text);
  return label ? { at: text(value.at), text: label, ok: value.ok !== false } : null;
}

function invoice(value: unknown): KsefInvoice | null {
  if (!isRecord(value)) return null;
  const number = text(value.number);
  if (!number) return null;
  return {
    number, ksefNumber: text(value.ksef_number) ?? "", supplier: text(value.supplier) ?? "",
    total: typeof value.total === "number" ? String(value.total) : text(value.total) ?? "", currency: text(value.currency) ?? "EUR", linked: value.linked === true,
  };
}

export function parseKsefConfig(config: unknown): KsefConfig {
  const raw = isRecord(config) ? config : {};
  return {
    env: text(raw.env), api: text(raw.api), nip: text(raw.nip), company: text(raw.company),
    phase: isPhase(raw.phase) ? raw.phase : "idle",
    steps: Array.isArray(raw.steps) ? raw.steps.map(step).filter((entry): entry is KsefStep => entry !== null) : [],
    issued: count(raw.issued), received: count(raw.received), imported: count(raw.imported), linked: count(raw.linked),
    linkedTotal: typeof raw.linked_total === "number" ? String(raw.linked_total) : text(raw.linked_total),
    onFile: count(raw.on_file), unpaid: count(raw.unpaid),
    unpaidTotal: typeof raw.unpaid_total === "number" ? String(raw.unpaid_total) : text(raw.unpaid_total),
    invoices: Array.isArray(raw.invoices) ? raw.invoices.map(invoice).filter((entry): entry is KsefInvoice => entry !== null) : [],
  };
}

export function apiHost(api: string | null): string | null {
  if (!api) return null;
  try { return new URL(api).host; } catch { return api; }
}

/** "7 invoices received · 5 matched to payments (€3,412.80) · tax-document risk reduced" */
export function ksefSummary(config: KsefConfig, format: (amount: number, currency?: string) => string): string {
  const received = config.received || config.imported;
  const parts = [`${received} invoice${received === 1 ? "" : "s"} received`];
  if (config.linked > 0) {
    const total = config.linkedTotal !== null && Number.isFinite(Number(config.linkedTotal)) ? ` (${format(Number(config.linkedTotal))})` : "";
    parts.push(`${config.linked} matched to payments${total}`, "tax-document risk reduced");
  }
  if (config.onFile > 0) parts.push(`${config.onFile} already on file`);
  if (config.unpaid > 0) {
    const total = config.unpaidTotal !== null && Number.isFinite(Number(config.unpaidTotal)) ? ` (${format(Number(config.unpaidTotal))})` : "";
    parts.push(`${config.unpaid} new to pay${total}`);
  }
  return parts.join(" · ");
}

/** Decimal string -> display amount; falls back to the raw string for odd input. */
export function invoiceTotal(entry: Pick<KsefInvoice, "total" | "currency">, format: (amount: number, currency?: string) => string): string {
  const amount = Number(entry.total);
  if (entry.total === "" || !Number.isFinite(amount)) return entry.total || "—";
  try { return format(amount, entry.currency); } catch { return `${entry.total} ${entry.currency}`; }
}
