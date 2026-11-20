import { test } from "node:test";
import assert from "node:assert/strict";
import { decimalCents, displayCents, financePeriod } from "../lib/finance-model.ts";
import { authorized } from "../lib/auth.ts";
import { isDevelopmentPreview } from "../lib/development-preview.ts";

test("sample-data access is opt-in and never bypasses production authentication", () => {
  const environment: Record<string, string | undefined> = process.env;
  const original = { nodeEnv: environment.NODE_ENV, preview: process.env.DASHBOARD_PREVIEW };
  try {
    process.env.DASHBOARD_PREVIEW = "1";
    environment.NODE_ENV = "production";
    assert.equal(isDevelopmentPreview(), false);
    assert.equal(authorized(null), false);
    environment.NODE_ENV = "test";
    assert.equal(isDevelopmentPreview(), false);
    environment.NODE_ENV = "development";
    assert.equal(isDevelopmentPreview(), true);
    assert.equal(authorized(null), true);
    process.env.DASHBOARD_PREVIEW = "0";
    assert.equal(isDevelopmentPreview(), false);
    assert.equal(authorized(null), false);
  } finally {
    if (original.nodeEnv === undefined) delete environment.NODE_ENV; else environment.NODE_ENV = original.nodeEnv;
    if (original.preview === undefined) delete process.env.DASHBOARD_PREVIEW; else process.env.DASHBOARD_PREVIEW = original.preview;
  }
});

test("money aggregation preserves cents, categories and inflows", () => {
  const period = financePeriod([
    { category: "revenue", count: "3", income: "30", expense: "0" },
    { category: "payroll", count: "2", income: "0", expense: "10" },
    { category: null, count: "1", income: "0", expense: "20" },
  ]);
  assert.equal(period.transactions, 6);
  assert.equal(period.totalRevenue, 0.30);
  assert.equal(period.totalExpenses, 0.30);
  assert.deepEqual(period.expenses.map(item => item.name), ["Payroll", "Other"]);
  assert.deepEqual(period.expenses.map(item => item.amount), [0.10, 0.20]);
  assert.equal(financePeriod([]).totalExpenses, 0);
  assert.deepEqual(financePeriod([]).expenses, []);
});

test("API decimals handle signs and reject malformed or unsafe money", () => {
  assert.equal(decimalCents("-0.01"), -1n);
  assert.equal(displayCents(decimalCents("123456.78")), 123456.78);
  assert.throws(() => decimalCents("1.001"));
  assert.throws(() => decimalCents("NaN"));
  assert.throws(() => displayCents("9007199254740992"));
});

test("authentication fails closed and checks the full credentials", () => {
  const username = process.env.DASHBOARD_USERNAME;
  const password = process.env.DASHBOARD_PASSWORD;
  try {
    process.env.DASHBOARD_USERNAME = "unit-test";
    process.env.DASHBOARD_PASSWORD = "a:password";
    const basic = (value: string) => `Basic ${Buffer.from(value).toString("base64")}`;
    assert.equal(authorized(basic("unit-test:a:password")), true);
    assert.equal(authorized(basic("unit-test:wrong")), false);
    assert.equal(authorized(basic("unit-test:a:password:extra")), false);
    assert.equal(authorized(null), false);
    assert.equal(authorized("Bearer token"), false);
    delete process.env.DASHBOARD_PASSWORD;
    assert.equal(authorized(basic("unit-test:a:password")), false);
  } finally {
    if (username === undefined) delete process.env.DASHBOARD_USERNAME; else process.env.DASHBOARD_USERNAME = username;
    if (password === undefined) delete process.env.DASHBOARD_PASSWORD; else process.env.DASHBOARD_PASSWORD = password;
  }
});

test("invoice payload keeps cents and maps the document", async () => {
  const { parseTransactionInvoice } = await import("../lib/invoice-model.ts");
  const parsed = parseTransactionInvoice({
    transaction: { id: "tx1", booking_date: "2026-10-02", reference: "FLV-1", counterparty: "Fattoria", amount: "-120.10", currency: "EUR" },
    invoice: { id: "inv", number: "FLV-1", supplier: "Fattoria", issue_date: "2026-10-01", due_date: null, total: "120.10", currency: "EUR", source: "db", category: null },
    lines: [{ position: 0, description: "Chianti", quantity: "10.0000", unit_price: "12.0100", amount: "120.10", category: "beverages" }],
    document: { task_id: "SC-1", filed_at: "2026-10-02T10:00:00+00:00", source: "invoicing_pg", text_excerpt: "INVOICE", evidence: { name: "FLV-1.pdf" } },
    task_id: "SC-1",
  });
  assert.equal(parsed.transaction.amount, -120.1);
  assert.equal(parsed.lines[0].unitPrice, 12.01);
  assert.equal(parsed.document?.textExcerpt, "INVOICE");
  assert.throws(() => parseTransactionInvoice({ transaction: {}, invoice: {}, lines: [] }));
});
