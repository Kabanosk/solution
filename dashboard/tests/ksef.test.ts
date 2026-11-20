import { test } from "node:test";
import assert from "node:assert/strict";
import { apiHost, invoiceTotal, ksefSummary, parseKsefConfig } from "../lib/ksef.ts";

const format = (amount: number, currency = "EUR") => `${currency} ${amount.toFixed(2)}`;

test("parseKsefConfig tolerates junk", () => {
  for (const junk of [null, undefined, 5, "x", [], {}, { phase: "weird", steps: "no", invoices: [1, null, {}] }]) {
    const config = parseKsefConfig(junk);
    assert.equal(config.phase, "idle");
    assert.deepEqual(config.steps, []);
    assert.deepEqual(config.invoices, []);
    assert.equal(config.received, 0);
  }
});

test("parseKsefConfig reads the backend contract", () => {
  const config = parseKsefConfig({
    env: "test", api: "https://api-test.ksef.mf.gov.pl/v2", nip: "5621254863", phase: "done",
    steps: [{ at: "2026-11-21T00:41:02Z", text: "Signed in", ok: true }, { text: "Failed", ok: false }, { ok: true }],
    issued: 7, received: 7, imported: 7, linked: 5, linked_total: "3412.80",
    invoices: [{ number: "AS-1", ksef_number: "5621254863-20261121-01B7-6F", supplier: "Asimex", total: "623.48", currency: "EUR", linked: true }, { number: "AS-2" }],
  });
  assert.equal(config.phase, "done");
  assert.equal(config.steps.length, 2);
  assert.equal(config.steps[1].ok, false);
  assert.equal(config.invoices.length, 2);
  assert.equal(config.invoices[0].linked, true);
  assert.equal(config.invoices[1].linked, false);
  assert.equal(apiHost(config.api), "api-test.ksef.mf.gov.pl");
  assert.equal(ksefSummary(config, format), "7 invoices received · 5 matched to payments (EUR 3412.80) · tax-document risk reduced");
  assert.equal(invoiceTotal(config.invoices[0], format), "EUR 623.48");
  assert.equal(invoiceTotal({ total: "", currency: "EUR" }, format), "—");
});

test("ksefSummary with files on record and new payables", () => {
  const config = parseKsefConfig({ received: 8, on_file: 5, unpaid: 3, unpaid_total: "2411.41" });
  assert.equal(ksefSummary(config, format), "8 invoices received · 5 already on file · 3 new to pay (EUR 2411.41)");
});

test("ksefSummary without matches", () => {
  assert.equal(ksefSummary(parseKsefConfig({ received: 1 }), format), "1 invoice received");
});
