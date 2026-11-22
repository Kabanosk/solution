import { test } from "node:test";
import assert from "node:assert/strict";
import { guardrailLocksAccess, guardrailUnlockAudit, parseUnlock } from "../lib/guardrails-model.ts";
import { listGuardrailLocks, unlockGuardrailPerson, type GuardrailsDb } from "../lib/guardrails-store.ts";

const LEGACY = { organizationId: "00000000-0000-4000-8000-000000000001", schema: "public", legacy: true, employeeId: "vincenzo", email: "owner@example.com", name: "Vincenzo Amalfi" };
const TENANT_ID = "6f1c2a8e-1b2c-4d3e-8f90-123456789abc";
const TENANT = { organizationId: TENANT_ID, schema: `tenant_${TENANT_ID.replaceAll("-", "")}`, legacy: false, employeeId: "", email: "owner@example.com", name: " " };

type LockRow = { id: number; person: string; name: string; locked_at: Date; locked_until: Date; rails: string; unlocked_at: Date | null };

/** A pg pool stand-in over a few lock rows; every statement is recorded in order. */
function fakeDb(locks: LockRow[], fail = false) {
  const sql: { text: string; values: unknown[] }[] = [];
  const query = async (text: string, values: unknown[] = []) => {
    sql.push({ text, values });
    if (fail) throw Object.assign(new Error("relation does not exist"), { code: "42P01" });
    if (text.includes("FROM guardrail_locks")) return { rows: locks.filter(l => !l.unlocked_at) };
    if (text.startsWith("UPDATE guardrail_locks")) {
      const hit = locks.filter(l => l.person === values[0] && !l.unlocked_at);
      hit.forEach(l => { l.unlocked_at = new Date(); });
      return { rows: hit.map(l => ({ id: l.id })) };
    }
    return { rows: [] };
  };
  const db: GuardrailsDb = { query, connect: async () => ({ query, release: () => undefined }) };
  return { db, sql };
}
const lock = (id: number, person: string, name = person, rails = "regex check input"): LockRow =>
  ({ id, person, name, locked_at: new Date("2026-11-22T10:00:00Z"), locked_until: new Date("2026-11-23T10:00:00Z"), rails, unlocked_at: null });

test("only the workspace owner may list or lift locks", () => {
  assert.equal(guardrailLocksAccess("owner"), null);
  for (const role of ["member", "admin", "", undefined, null]) assert.deepEqual(guardrailLocksAccess(role as string | undefined), { status: 403, error: "Workspace owner access required." });
});

test("an unlock names one person", () => {
  assert.equal(parseUnlock({ person: "vanessa" }), "vanessa");
  assert.equal(parseUnlock({ person: "  vanessa " }), "vanessa");
  for (const value of [null, {}, [], { person: "" }, { person: 3 }, { person: "x".repeat(200) }]) assert.throws(() => parseUnlock(value));
});

test("the unlock audit row is guardrail_unlock, ALLOW, requested by the owner", () => {
  assert.deepEqual(guardrailUnlockAudit("vanessa", "vincenzo"), { actor: "dashboard", requester: "vincenzo", role: "owner", action: "guardrail_unlock", target: "guardrails:vanessa", resource: "access to Scout restored for vanessa", decision: "ALLOW", policy: "user-input", reason: "unlocked by owner" });
});

test("the list shows the active locks with name, until and rails, read inside the workspace schema", async () => {
  const { db, sql } = fakeDb([lock(1, "vanessa", "Vanessa Rossi")]);
  const rows = await listGuardrailLocks(db, TENANT);
  assert.deepEqual(rows, [{ id: 1, person: "vanessa", name: "Vanessa Rossi", lockedAt: "2026-11-22T10:00:00.000Z", lockedUntil: "2026-11-23T10:00:00.000Z", rails: "regex check input" }]);
  assert.deepEqual(sql.find(s => s.text.includes("set_config('search_path'"))?.values, [TENANT.schema]);
  const select = sql.find(s => s.text.includes("FROM guardrail_locks"))!.text;
  assert.match(select, /unlocked_at IS NULL/);
  assert.match(select, /locked_until > now\(\)/);
  assert.equal(sql[0].text, "BEGIN");
  assert.equal(sql.at(-1)?.text, "COMMIT");
});

test("nobody locked means an empty list", async () => {
  assert.deepEqual(await listGuardrailLocks(fakeDb([]).db, LEGACY), []);
});

test("an unregistered schema is refused before anything is read or written", async () => {
  const { db, sql } = fakeDb([]);
  await assert.rejects(listGuardrailLocks(db, { ...TENANT, schema: "public" }), /Invalid organization/);
  await assert.rejects(unlockGuardrailPerson(db, { ...TENANT, schema: "public" }, "vanessa"), /Invalid organization/);
  assert.equal(sql.length, 0);
});

test("unlocking stamps unlocked_at and unlocked_by and writes one guardrail_unlock audit row in the same transaction", async () => {
  const { db, sql } = fakeDb([lock(1, "vanessa"), lock(2, "rosa")]);
  assert.equal(await unlockGuardrailPerson(db, LEGACY, "vanessa"), 1);
  const update = sql.find(s => s.text.startsWith("UPDATE guardrail_locks"))!;
  assert.deepEqual(update.values, ["vanessa", "vincenzo"]); // the owner's employee id, like every audit requester
  assert.match(update.text, /unlocked_at = now\(\)/);
  const audits = sql.filter(s => s.text.startsWith("INSERT INTO audit_log"));
  assert.equal(audits.length, 1);
  assert.deepEqual(audits[0].values, ["dashboard", "vincenzo", "owner", "guardrail_unlock", "guardrails:vanessa", "access to Scout restored for vanessa", "ALLOW", "user-input", "unlocked by owner"]);
  const texts = sql.map(s => s.text);
  assert.ok(texts.indexOf("COMMIT") > texts.indexOf(audits[0].text) && texts.indexOf(audits[0].text) > texts.indexOf(update.text));
  assert.deepEqual((await listGuardrailLocks(db, LEGACY)).map(l => l.person), ["rosa"]); // only the named person was freed
});

test("unlocking someone who is not locked changes nothing and writes no audit row; the e-mail stands in for a missing employee id", async () => {
  const { db, sql } = fakeDb([lock(1, "vanessa")]);
  assert.equal(await unlockGuardrailPerson(db, TENANT, "rosa"), 0);
  assert.equal(sql.filter(s => s.text.startsWith("INSERT INTO audit_log")).length, 0);
  assert.equal(await unlockGuardrailPerson(db, TENANT, "vanessa"), 1);
  assert.deepEqual(sql.find(s => s.text.startsWith("UPDATE guardrail_locks") && (s.values[0] === "vanessa"))?.values, ["vanessa", "owner@example.com"]);
});

test("a database error rolls the unlock back", async () => {
  const { db, sql } = fakeDb([lock(1, "vanessa")], true);
  await assert.rejects(unlockGuardrailPerson(db, TENANT, "vanessa"));
  assert.ok(sql.some(s => s.text === "ROLLBACK"));
});
