import { parseKsefConfig } from "./ksef.ts";

/** Explicit display-only metadata. Never serialize arbitrary integration config/credentials. */
export function connectionDisplayConfig(id: string, input: unknown): Record<string, unknown> {
  const raw = input && typeof input === "object" && !Array.isArray(input) ? input as Record<string, unknown> : {};
  if (id === "slack") {
    const scopes = (value: unknown) => Array.isArray(value) ? value.filter((scope): scope is string => typeof scope === "string" && /^[a-z_]+:[a-z_.]+$/.test(scope)).slice(0, 100) : [];
    return { scopes: scopes(raw.scopes), missing_scopes: scopes(raw.missing_scopes) };
  }
  if (id !== "ksef") return {};
  const parsed = parseKsefConfig(raw);
  let api: string | null = null;
  try { if (parsed.api) api = new URL(parsed.api).origin; } catch { /* Invalid URLs are not display metadata. */ }
  return {
    env: parsed.env, api, nip: parsed.nip, company: parsed.company, phase: parsed.phase,
    steps: parsed.steps, issued: parsed.issued, received: parsed.received, imported: parsed.imported,
    linked: parsed.linked, linked_total: parsed.linkedTotal, on_file: parsed.onFile,
    unpaid: parsed.unpaid, unpaid_total: parsed.unpaidTotal,
    invoices: parsed.invoices.map(invoice => ({ number: invoice.number, ksef_number: invoice.ksefNumber, supplier: invoice.supplier, total: invoice.total, currency: invoice.currency, linked: invoice.linked })),
  };
}
