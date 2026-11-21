import { test } from "node:test";
import assert from "node:assert/strict";
import { GUARDRAILS_DEFAULT, GUARDRAILS_POLL_MS, guardrailsAudit, guardrailsBanner, parseGuardrailsToggle, watchGuardrails } from "../lib/guardrails-model.ts";
import { readGuardrails, saveGuardrails, type GuardrailsDb } from "../lib/guardrails-store.ts";

const LEGACY = { organizationId: "00000000-0000-4000-8000-000000000001", schema: "public", legacy: true, employeeId: "vincenzo", email: "owner@example.com", name: "Vincenzo Amalfi" };
const TENANT_ID = "6f1c2a8e-1b2c-4d3e-8f90-123456789abc";
const TENANT = { organizationId: TENANT_ID, schema: `tenant_${TENANT_ID.replaceAll("-", "")}`, legacy: false, employeeId: "", email: "owner@example.com", name: " " };

/** A pg pool stand-in: one settings row per organization, every statement recorded. */
function fakeDb(initial: Record<string, boolean> = {}, fail = false) {
  const rows = new Map(Object.entries(initial).map(([id, enabled]) => [id, { enabled, updated_by: null as string | null, updated_at: new Date("2026-11-21T10:00:00Z") }]));
  const sql: { text: string; values: unknown[] }[] = [];
  const query = async (text: string, values: unknown[] = []) => {
    sql.push({ text, values });
    if (fail) throw Object.assign(new Error("relation does not exist"), { code: "42P01" });
    if (text.startsWith("INSERT INTO scout_auth.guardrails_settings")) { if (!rows.has(values[0] as string)) rows.set(values[0] as string, { enabled: true, updated_by: null, updated_at: new Date() }); return { rows: [] }; }
    if (text.startsWith("SELECT enabled") || text.startsWith("SELECT enabled,updated_by")) { const row = rows.get(values[0] as string); return { rows: row ? [row] : [] }; }
    if (text.startsWith("UPDATE scout_auth.guardrails_settings")) { rows.set(values[0] as string, { enabled: values[1] as boolean, updated_by: values[2] as string, updated_at: new Date("2026-11-21T11:00:00Z") }); return { rows: [] }; }
    return { rows: [] };
  };
  const db: GuardrailsDb = { query, connect: async () => ({ query, release: () => undefined }) };
  return { db, sql, rows };
}

test("a toggle needs an explicit boolean", () => {
  assert.equal(parseGuardrailsToggle({ enabled: false }), false);
  assert.equal(parseGuardrailsToggle({ enabled: true }), true);
  for (const value of [null, {}, [], { enabled: "false" }, { enabled: 0 }]) assert.throws(() => parseGuardrailsToggle(value));
});

test("the audit row matches the backend's guardrails_toggled row", () => {
  assert.deepEqual(guardrailsAudit(false, "vincenzo"), { actor: "dashboard", requester: "vincenzo", role: "owner", action: "guardrails_toggled", target: "guardrails:workspace", resource: "NeMo guardrails turned OFF", decision: "ALLOW", policy: "guardrails-switch", reason: "OFF" });
  assert.equal(guardrailsAudit(true, "x").reason, "ON");
});

test("the warning banner shows only to owners and only when the switch is known to be off", () => {
  assert.equal(guardrailsBanner("owner", { ...GUARDRAILS_DEFAULT, enabled: false }), true);
  assert.equal(guardrailsBanner("owner", GUARDRAILS_DEFAULT), false);
  assert.equal(guardrailsBanner("owner", null), false); // unknown (loading or unavailable): the backend counts it as ON
  assert.equal(guardrailsBanner("member", { ...GUARDRAILS_DEFAULT, enabled: false }), false);
});

test("no row means ON", async () => {
  const { db } = fakeDb();
  assert.deepEqual(await readGuardrails(db, TENANT_ID), GUARDRAILS_DEFAULT);
});

test("turning it off stores who and when and writes one audit row in the workspace schema", async () => {
  const { db, sql, rows } = fakeDb();
  const saved = await saveGuardrails(db, TENANT, false);
  assert.equal(saved.enabled, false);
  assert.equal(saved.updatedBy, "owner@example.com"); // no name and no employee id: the e-mail stands for the owner
  assert.equal(rows.get(TENANT_ID)?.enabled, false);
  const texts = sql.map(s => s.text);
  assert.equal(texts[0], "BEGIN");
  assert.ok(texts.some(t => t.includes("FOR UPDATE")));
  const path = sql.find(s => s.text.includes("set_config('search_path'"));
  assert.deepEqual(path?.values, [TENANT.schema]);
  const audits = sql.filter(s => s.text.startsWith("INSERT INTO audit_log"));
  assert.equal(audits.length, 1);
  assert.deepEqual(audits[0].values, ["dashboard", "owner@example.com", "owner", "guardrails_toggled", "guardrails:workspace", "NeMo guardrails turned OFF", "ALLOW", "guardrails-switch", "OFF"]);
  assert.ok(texts.indexOf("COMMIT") > texts.indexOf(audits[0].text));
});

test("saving the state it already has writes nothing and no audit row", async () => {
  const { db, sql } = fakeDb({ [LEGACY.organizationId]: true });
  assert.equal((await saveGuardrails(db, LEGACY, true)).enabled, true);
  assert.equal(sql.filter(s => s.text.startsWith("UPDATE") || s.text.startsWith("INSERT INTO audit_log")).length, 0);
  const off = await saveGuardrails(db, LEGACY, false);
  assert.equal(off.updatedBy, "Vincenzo Amalfi"); // the name next to the switch, the employee id in the audit row
  assert.equal(sql.filter(s => s.text.startsWith("INSERT INTO audit_log")).at(-1)?.values[1], "vincenzo");
  assert.deepEqual(sql.find(s => s.text.includes("set_config('search_path'"))?.values, ["public"]);
});

test("an unregistered schema is refused before any write and a database error rolls back", async () => {
  const { db, sql } = fakeDb();
  await assert.rejects(saveGuardrails(db, { ...TENANT, schema: "public" }, false), /Invalid organization/);
  assert.equal(sql.length, 0);
  const broken = fakeDb({}, true);
  await assert.rejects(saveGuardrails(broken.db, TENANT, false));
  assert.ok(broken.sql.some(s => s.text === "ROLLBACK"));
});

/** A page stand-in: visibility, focus and one interval timer. */
function fakePage() {
  const listeners = new Map<string, Set<() => void>>();
  const target = (prefix: string) => ({
    addEventListener: (type: string, fn: () => void) => { const key = `${prefix}:${type}`; if (!listeners.has(key)) listeners.set(key, new Set()); listeners.get(key)!.add(fn); },
    removeEventListener: (type: string, fn: () => void) => { listeners.get(`${prefix}:${type}`)?.delete(fn); },
  });
  const timers = new Map<number, { fn: () => void; ms: number }>(); let next = 1;
  const doc = { ...target("doc"), visibilityState: "visible" };
  const env = { doc, win: target("win"), setInterval: (fn: () => void, ms: number) => { timers.set(next, { fn, ms }); return next++; }, clearInterval: (id: unknown) => { timers.delete(id as number); } };
  const fire = (key: string) => listeners.get(key)?.forEach(fn => fn());
  return { env, doc, timers, fire, listeners };
}

test("the switch is re-read periodically while the page is visible, and on focus and when the tab comes back", () => {
  const page = fakePage(); let reads = 0;
  const stop = watchGuardrails(() => { reads++; }, page.env);
  assert.ok(GUARDRAILS_POLL_MS >= 10000 && GUARDRAILS_POLL_MS <= 15000);
  const [timer] = [...page.timers.values()];
  assert.equal(timer.ms, GUARDRAILS_POLL_MS);
  timer.fn(); assert.equal(reads, 1); // an OFF set elsewhere (another owner, guard-reset) shows without a reload
  page.doc.visibilityState = "hidden"; timer.fn(); page.fire("doc:visibilitychange"); assert.equal(reads, 1); // a hidden tab costs nothing
  page.doc.visibilityState = "visible"; page.fire("doc:visibilitychange"); assert.equal(reads, 2);
  page.fire("win:focus"); assert.equal(reads, 3);
  stop();
  assert.equal(page.timers.size, 0);
  page.fire("win:focus"); page.fire("doc:visibilitychange"); assert.equal(reads, 3);
});
