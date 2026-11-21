import { test, after } from "node:test";
import assert from "node:assert/strict";
import { Pool } from "pg";
import { randomUUID } from "node:crypto";
import { AuthStore } from "../lib/auth-store.ts";
const pool = process.env.SCOUT_AUTH_TEST_DATABASE_URL ? new Pool({ connectionString: process.env.SCOUT_AUTH_TEST_DATABASE_URL }) : null;
after(async () => { await pool?.end(); });
const run = randomUUID().slice(0, 8);

async function orphanedTenantSchemas() {
  return (await pool!.query(`SELECT nspname FROM pg_namespace n WHERE nspname ~ '^tenant_[0-9a-f]{32}$'
    AND NOT EXISTS (SELECT 1 FROM scout_auth.organizations o WHERE o.schema_name=n.nspname) ORDER BY nspname`)).rows;
}

test("verified Google registration creates only a new empty organization and its owner", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const identity = { subject: `new-google-${run}`, email: `google-${run}@example.test`, name: "Google Owner" };
  const result = await store.registerGoogle({ name: "Google Studio", slug: `google-${run}` }, identity);
  assert.equal(result.session.slug, `google-${run}`);
  assert.equal((await store.session(result.token, result.session.slug))?.role, "owner");
  assert.equal(await store.session(result.token, "pizzeria-amalfi"), null);
  const user = (await pool!.query("SELECT password_hash,google_subject FROM scout_auth.users WHERE id=$1", [result.session.userId])).rows[0];
  assert.equal(user.password_hash, null);
  assert.equal(user.google_subject, identity.subject);
  assert.equal((await pool!.query(`SELECT count(*)::int AS n FROM "${result.session.schema}".transactions`)).rows[0].n, 0);
  assert.equal((await store.googleLogin(result.session.slug, identity.subject, identity.email))?.session.userId, result.session.userId);
  await assert.rejects(store.registerGoogle({ name: "Amalfi", slug: "pizzeria-amalfi" }, identity));
});

test("Google signup never implicitly links an existing password account by email", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const email = `password-${run}@example.test`;
  await store.register({ name: "Password Studio", slug: `password-${run}`, ownerName: "Owner", email, password: "local-only strong password" });
  await assert.rejects(store.registerGoogle({ name: "Attempt", slug: `attempt-${run}` }, { subject: `unknown-${run}`, email, name: "Owner" }), /link Google/i);
  assert.equal((await pool!.query("SELECT google_subject FROM scout_auth.users WHERE email=$1", [email])).rows[0].google_subject, null);
  assert.equal(await store.organization(`attempt-${run}`), null);
});

test("Google registration state is browser-bound and creates nothing before consent", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const draft = { name: "Pending Studio", slug: `pending-${run}` };
  const flow = await store.startGoogleRegistration(draft, "registration-browser");
  assert.equal(await store.organization(draft.slug), null);
  assert.equal(await store.consumeOAuth(flow.state, "wrong-browser"), null);
  assert.deepEqual((await store.consumeOAuth(flow.state, "registration-browser"))?.registration, draft);
  assert.equal(await store.consumeOAuth(flow.state, "registration-browser"), null);
});

test("a linked Google account can own three organizations but a fourth leaves no partial registration", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const identity = { subject: `quota-${run}`, email: `quota-${run}@example.test`, name: "Google Owner" };
  const registrations = [];
  for (let i = 1; i <= 3; i++) {
    const result = await store.registerGoogle({ name: `Studio ${i}`, slug: `quota-${i}-${run}` }, identity);
    registrations.push(result);
    assert.equal(result.session.userId, registrations[0].session.userId);
    assert.equal((await store.session(result.token, result.session.slug))?.role, "owner");
  }
  assert.equal(new Set(registrations.map(result => result.session.schema)).size, 3);
  const orphans = await orphanedTenantSchemas();
  await assert.rejects(store.registerGoogle({ name: "Fourth Studio", slug: `quota-4-${run}` }, identity), /up to three/i);
  assert.equal(await store.organization(`quota-4-${run}`), null);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.memberships WHERE user_id=$1 AND role='owner'", [registrations[0].session.userId])).rows[0].n, 3);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.sessions WHERE user_id=$1", [registrations[0].session.userId])).rows[0].n, 3);
  assert.deepEqual(await orphanedTenantSchemas(), orphans);
});

test("concurrent registrations by one linked Google account cannot overrun the last ownership slot", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const identity = { subject: `race-quota-${run}`, email: `race-quota-${run}@example.test`, name: "Google Owner" };
  const first = await store.registerGoogle({ name: "First Studio", slug: `race-quota-1-${run}` }, identity);
  await store.registerGoogle({ name: "Second Studio", slug: `race-quota-2-${run}` }, identity);
  const drafts = [3, 4].map(i => ({ name: `Studio ${i}`, slug: `race-quota-${i}-${run}` }));
  const orphans = await orphanedTenantSchemas();
  const results = await Promise.allSettled(drafts.map(draft => store.registerGoogle(draft, identity)));
  assert.equal(results.filter(result => result.status === "fulfilled").length, 1);
  const rejected = results.find(result => result.status === "rejected");
  assert.ok(rejected && rejected.status === "rejected");
  assert.match(rejected.reason.message, /up to three/i);
  for (let i = 0; i < results.length; i++) {
    const result = results[i];
    if (result.status === "fulfilled") {
      assert.equal(result.value.session.userId, first.session.userId);
      assert.equal((await store.session(result.value.token, drafts[i].slug))?.role, "owner");
    } else {
      assert.equal(await store.organization(drafts[i].slug), null);
    }
  }
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.memberships WHERE user_id=$1 AND role='owner'", [first.session.userId])).rows[0].n, 3);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.sessions WHERE user_id=$1", [first.session.userId])).rows[0].n, 3);
  assert.deepEqual(await orphanedTenantSchemas(), orphans);
});

test("concurrent Google registrations for one slug roll back the losing account and tenant", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const draft = { name: "Contested Studio", slug: `race-slug-${run}` };
  const identities = [1, 2].map(i => ({ subject: `race-slug-${i}-${run}`, email: `race-slug-${i}-${run}@example.test`, name: `Owner ${i}` }));
  const orphans = await orphanedTenantSchemas();
  const results = await Promise.allSettled(identities.map(identity => store.registerGoogle(draft, identity)));
  assert.equal(results.filter(result => result.status === "fulfilled").length, 1);
  assert.equal(results.filter(result => result.status === "rejected").length, 1);
  const winner = results.find(result => result.status === "fulfilled");
  assert.ok(winner && winner.status === "fulfilled");
  const users = (await pool!.query("SELECT id,email FROM scout_auth.users WHERE email=ANY($1::text[])", [identities.map(identity => identity.email)])).rows;
  assert.equal(users.length, 1);
  assert.equal(users[0].id, winner.value.session.userId);
  assert.equal((await store.session(winner.value.token, draft.slug))?.userId, users[0].id);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.memberships WHERE organization_id=$1", [winner.value.session.organizationId])).rows[0].n, 1);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.sessions WHERE organization_id=$1", [winner.value.session.organizationId])).rows[0].n, 1);
  assert.deepEqual(await orphanedTenantSchemas(), orphans);
});

test("expired Google registration consent cannot return its organization draft", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const draft = { name: "Expired Studio", slug: `expired-${run}` };
  const flow = await store.startGoogleRegistration(draft, "expired-browser");
  const expired = await pool!.query("UPDATE scout_auth.oauth_transactions SET expires_at=now()-interval '1 second' WHERE registration->>'slug'=$1", [draft.slug]);
  assert.equal(expired.rowCount, 1);
  assert.equal(await store.consumeOAuth(flow.state, "expired-browser"), null);
  assert.equal(await store.organization(draft.slug), null);
});

test("malformed organization drafts are rejected both before consent and at Google registration", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const identity = { subject: `invalid-${run}`, email: `invalid-${run}@example.test`, name: "Google Owner" };
  const drafts = [
    { name: "   ", slug: `invalid-empty-${run}` },
    { name: "x".repeat(101), slug: `invalid-long-${run}` },
    { name: "Invalid URL", slug: `invalid-${run};DROP SCHEMA public` },
    { name: "Long URL", slug: "x".repeat(81) },
  ];
  const orphans = await orphanedTenantSchemas();
  for (const draft of drafts) {
    await assert.rejects(store.startGoogleRegistration(draft, "invalid-browser"), /organization name and URL/i);
    await assert.rejects(store.registerGoogle(draft, identity), /organization name, URL/i);
  }
  const slugs = drafts.map(draft => draft.slug);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.organizations WHERE slug=ANY($1::text[])", [slugs])).rows[0].n, 0);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.oauth_transactions WHERE registration->>'slug'=ANY($1::text[])", [slugs])).rows[0].n, 0);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.users WHERE email=$1", [identity.email])).rows[0].n, 0);
  assert.deepEqual(await orphanedTenantSchemas(), orphans);
});

test("Google registration never reuses an account with a mismatched subject or email", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const identity = { subject: `linked-${run}`, email: `linked-${run}@example.test`, name: "Google Owner" };
  const original = await store.registerGoogle({ name: "Linked Studio", slug: `linked-${run}` }, identity);
  const orphans = await orphanedTenantSchemas();
  await assert.rejects(store.registerGoogle({ name: "Other Subject", slug: `other-subject-${run}` }, { ...identity, subject: `other-${run}` }), /link Google/i);
  await assert.rejects(store.registerGoogle({ name: "Other Email", slug: `other-email-${run}` }, { ...identity, email: `other-${run}@example.test` }));
  assert.equal(await store.googleLogin(original.session.slug, `other-${run}`, identity.email), null);
  assert.equal(await store.googleLogin(original.session.slug, identity.subject, `other-${run}@example.test`), null);
  assert.equal((await store.googleLogin(original.session.slug, identity.subject, identity.email))?.session.userId, original.session.userId);
  assert.equal(await store.organization(`other-subject-${run}`), null);
  assert.equal(await store.organization(`other-email-${run}`), null);
  assert.deepEqual((await pool!.query("SELECT email,google_subject FROM scout_auth.users WHERE id=$1", [original.session.userId])).rows[0], { email: identity.email, google_subject: identity.subject });
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.users WHERE email=$1", [`other-${run}@example.test`])).rows[0].n, 0);
  assert.deepEqual(await orphanedTenantSchemas(), orphans);
});
