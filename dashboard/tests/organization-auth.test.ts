import { test } from "node:test";
import assert from "node:assert/strict";
import { validSlug, passwordHash, verifyPassword, tokenHash, sameOrigin, validateIdentity, registeredSchema } from "../lib/auth-crypto.ts";

test("organization slugs cannot shadow routes or become schema identifiers", () => {
  for (const slug of ["pizzeria-amalfi", "studio-42"]) assert.equal(validSlug(slug), true);
  for (const slug of ["api", "tasks", "healthz", "admin", "_next", "Foo", "a", "-foo", "foo--bar", "foo/../api", "a".repeat(64)]) assert.equal(validSlug(slug), false, slug);
});

test("only the reserved legacy organization can access public tables", () => {
  const org = "11111111-1111-4111-8111-111111111111";
  assert.equal(registeredSchema(org, "tenant_11111111111141118111111111111111", false), true);
  assert.equal(registeredSchema(org, "public", false), false);
  assert.equal(registeredSchema(org, "public", true), false);
  assert.equal(registeredSchema(org, "tenant_22222222222242228222222222222222", false), false);
  assert.equal(registeredSchema("00000000-0000-4000-8000-000000000001", "public", true), true);
});

test("password hashes are salted and reject wrong passwords and malformed storage", async () => {
  const first = await passwordHash("correct horse battery staple");
  const second = await passwordHash("correct horse battery staple");
  assert.notEqual(first, second);
  assert.equal(await verifyPassword("correct horse battery staple", first), true);
  assert.equal(await verifyPassword("wrong password", first), false);
  assert.equal(await verifyPassword("anything", "scrypt$junk"), false);
  await assert.rejects(passwordHash("short"));
});

test("session lookup hashes tokens rather than persisting bearer credentials", () => {
  assert.equal(tokenHash("test-token"), "4c5dc9b7708905f77f5e5d16316b5dfb425e68cb326dcd55a860e90a7707031e");
});

test("CSRF checks compare exact configured origins and reject missing or malformed origins", () => {
  assert.equal(sameOrigin("https://scout.example", "https://scout.example"), true);
  for (const origin of [null, "null", "https://evil.example", "http://scout.example", "https://scout.example.evil", "https://scout.example/path"]) assert.equal(sameOrigin(origin, "https://scout.example"), false);
});

test("Google identity requires verified email, intended audience, issuer, nonce, and expiry", () => {
  const claims = { sub: "google-user-1", email: "owner@example.com", email_verified: true, aud: "client-1", iss: "https://accounts.google.com", nonce: "nonce-1", exp: Math.floor(Date.now() / 1000) + 60 };
  assert.deepEqual(validateIdentity(claims, "client-1", "nonce-1"), { subject: "google-user-1", email: "owner@example.com" });
  for (const change of [{ email_verified: false }, { aud: "other" }, { iss: "https://evil.example" }, { nonce: "other" }, { exp: 1 }, { sub: "" }]) assert.throws(() => validateIdentity({ ...claims, ...change }, "client-1", "nonce-1"));
});
