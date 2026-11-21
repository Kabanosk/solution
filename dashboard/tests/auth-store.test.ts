import { test, after } from "node:test";
import assert from "node:assert/strict";
import { Pool } from "pg";
import { randomUUID } from "node:crypto";
const run = randomUUID().slice(0, 8);
const aSlug = `studio-a-${run}`, bSlug = `studio-b-${run}`, aEmail = `a-${run}@example.com`, bEmail = `b-${run}@example.com`;
import { AuthStore } from "../lib/auth-store.ts";
import { tokenHash } from "../lib/auth-crypto.ts";

const url = process.env.SCOUT_AUTH_TEST_DATABASE_URL;
const pool = url ? new Pool({ connectionString: url }) : null;
after(async () => { await pool?.end(); });

test("signup is atomic, rejects reserved/duplicate slugs, and never grants legacy ownership", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const input = { name: "Studio A", slug: aSlug, email: aEmail, ownerName: "Owner A", password: "correct horse battery staple" };
  const results = await Promise.allSettled([store.register(input), store.register(input)]);
  assert.equal(results.filter(r => r.status === "fulfilled").length, 1);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.organizations WHERE slug=$1", [aSlug])).rows[0].n, 1);
  await assert.rejects(store.register({ ...input, slug: "pizzeria-amalfi", email: "thief@example.com" }));
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.memberships WHERE organization_id='00000000-0000-4000-8000-000000000001'")).rows[0].n, 0);
});

test("exact slug lookup wins over lookalike display names", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const other = await store.register({ name: aSlug, slug: `lookalike-${run}`, email: `lookalike-${run}@example.com`, ownerName: "Owner", password: "correct horse battery staple" });
  assert.equal((await store.findOrganization(aSlug))?.slug, aSlug);
  assert.notEqual(other.session.slug, aSlug);
});

test("rate limits expire and stale security records are pruned", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const key = `limit-${run}`;
  assert.equal(await store.rateLimit(key, 1), true);
  assert.equal(await store.rateLimit(key, 1), false);
  await pool!.query("UPDATE scout_auth.rate_limits SET reset_at=now()-interval '1 second' WHERE key=$1", [tokenHash(key)]);
  assert.equal(await store.rateLimit(key, 1), true);
});

test("one account cannot provision unlimited organization schemas", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  for (const index of [1, 2]) await store.register({ name: `Extra ${index}`, slug: `extra-${run}-${index}`, email: aEmail, ownerName: "Owner", password: "correct horse battery staple" });
  await assert.rejects(store.register({ name: "Fourth", slug: `extra-${run}-3`, email: aEmail, ownerName: "Owner", password: "correct horse battery staple" }), /three organizations/);
  assert.equal((await pool!.query("SELECT count(*)::int AS n FROM scout_auth.organizations WHERE slug=$1", [`extra-${run}-3`])).rows[0].n, 0);
});

test("sessions require active membership of the exact organization, including after revocation", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const result = await store.register({ name: "Studio B", slug: bSlug, email: bEmail, ownerName: "Owner B", password: "another correct horse password" });
  assert.equal((await store.session(result.token, bSlug))?.slug, bSlug);
  assert.equal(await store.session(result.token, aSlug), null);
  assert.equal(await store.session(result.token, "pizzeria-amalfi"), null);
  assert.equal(await store.session("fabricated-token", bSlug), null);
  await pool!.query("DELETE FROM scout_auth.memberships WHERE user_id=$1", [result.session.userId]);
  assert.equal(await store.session(result.token, bSlug), null);
});

test("password login cannot join an organization and rejects wrong passwords", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  assert.equal(await store.login(aSlug, aEmail, "wrong"), null);
  assert.equal(await store.login("pizzeria-amalfi", aEmail, "correct horse battery staple"), null);
  const result = await store.login(aSlug, aEmail, "correct horse battery staple");
  assert.equal(result?.session.slug, aSlug);
  assert.notEqual(result?.token, undefined);
  await store.logout(result!.token);
  assert.equal(await store.session(result!.token, aSlug), null);
});

test("Google cannot link by email or implicitly join an existing organization", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  assert.equal(await store.googleLogin(aSlug, "google-new", aEmail), null);
  await pool!.query("UPDATE scout_auth.users SET google_subject=$1 WHERE email=$2", [`google-a-${run}`, aEmail]);
  assert.equal((await store.googleLogin(aSlug, `google-a-${run}`, aEmail))?.session.slug, aSlug);
  assert.equal(await store.googleLogin("pizzeria-amalfi", `google-a-${run}`, aEmail), null);
});

test("OAuth state is browser-bound, expires, and is consumed only once", { skip: !pool }, async () => {
  const store = new AuthStore(pool!);
  const flow = await store.startOAuth(aSlug, "browser-proof");
  assert.equal(await store.consumeOAuth(flow.state, "wrong-browser"), null);
  assert.equal((await store.consumeOAuth(flow.state, "browser-proof"))?.slug, aSlug);
  assert.equal(await store.consumeOAuth(flow.state, "browser-proof"), null);
  const expired = await store.startOAuth(aSlug, "browser-proof");
  await pool!.query("UPDATE scout_auth.oauth_transactions SET expires_at=now()-interval '1 second' WHERE state_hash=$1", [tokenHash(expired.state)]);
  assert.equal(await store.consumeOAuth(expired.state, "browser-proof"), null);
});
