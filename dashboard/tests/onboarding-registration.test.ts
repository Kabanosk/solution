import { test } from "node:test";
import assert from "node:assert/strict";
import type { Pool } from "pg";
import { AuthStore } from "../lib/auth-store.ts";

// A scripted pool: records every statement of the registration transaction in order. The real AuthStore code
// runs; only the database is replaced. Real-database coverage is in onboarding-registration-db.test.ts.
function scriptedPool(failOn?: RegExp) {
  const statements: { sql: string; params: unknown[] }[] = [];
  let released = false;
  const orgIds: string[] = [];
  const client = {
    async query(sql: string, params: unknown[] = []) {
      statements.push({ sql: sql.replace(/\s+/g, " ").trim(), params });
      if (failOn?.test(sql)) throw new Error("scripted failure");
      if (/INSERT INTO scout_auth.organizations/.test(sql)) orgIds.push(String(params[0]));
      if (/SELECT id,password_hash,google_subject FROM scout_auth.users/.test(sql)) return { rows: [], rowCount: 0 };
      if (/FROM scout_auth.memberships m/.test(sql)) return { rows: [{ userId: "u", organizationId: orgIds[0], slug: "setup-co", organizationName: "Setup Co", schema: `tenant_${orgIds[0].replaceAll("-", "")}`, name: "O", email: "o@example.test", employeeId: "u", role: "owner", legacy: false }], rowCount: 1 };
      return { rows: [], rowCount: 0 };
    },
    release() { released = true; },
  };
  const pool = { connect: async () => client, query: client.query } as unknown as Pool;
  return { pool, statements, released: () => released };
}
const register = (pool: Pool) => new AuthStore(pool).register({ name: "Setup Co", slug: "setup-co", email: "o@example.test", ownerName: "Owner", password: "a long enough password" });
const index = (statements: { sql: string }[], pattern: RegExp) => statements.findIndex(s => pattern.test(s.sql));

test("registration creates the pending workspace_setup row inside the same transaction, after provisioning", async () => {
  const { pool, statements } = scriptedPool();
  await register(pool);
  const insert = index(statements, /INSERT INTO scout_auth\.workspace_setup\(organization_id\) VALUES\(\$1\)$/);
  assert.ok(insert > 0, "workspace_setup insert missing");
  assert.ok(index(statements, /^BEGIN/) < index(statements, /provision_organization/));
  assert.ok(index(statements, /provision_organization/) < insert);
  assert.ok(insert < index(statements, /^COMMIT/));
  assert.equal(statements[insert].params.length, 1);
  assert.equal(statements[insert].params[0], statements[index(statements, /INSERT INTO scout_auth\.organizations/)].params[0]);
  // Only organization_id is supplied: backend column defaults own status/revision/steps.
  assert.doesNotMatch(statements[insert].sql, /status|revision|steps/i);
});

test("a failing setup insert rolls the whole registration back", async () => {
  const { pool, statements, released } = scriptedPool(/workspace_setup/);
  await assert.rejects(register(pool), /scripted failure/);
  assert.ok(index(statements, /^ROLLBACK/) > 0);
  assert.equal(index(statements, /^COMMIT/), -1);
  assert.ok(released());
});

test("ordinary login never writes workspace_setup", async () => {
  const { pool, statements } = scriptedPool();
  try { await new AuthStore(pool).login("setup-co", "o@example.test", "a long enough password"); } catch { /* unknown scripted user */ }
  assert.equal(index(statements, /workspace_setup/), -1);
});
