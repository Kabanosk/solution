import { test } from "node:test";
import assert from "node:assert/strict";
import { connectionDisplayConfig } from "../lib/connection-display.ts";

test("existing KSeF sync details survive without exporting connector credentials", () => {
  const raw = { phase: "done", api: "https://user:password@ksef.example/api?token=secret", received: 2, linked: 1, linked_total: "45.00", access_token: "private", credentials: { password: "private" }, invoices: [{ number: "INV-1", ksef_number: "K-1", total: "45.00", token: "private" }] };
  const result = connectionDisplayConfig("ksef", raw);
  assert.equal(result.phase, "done");
  assert.equal(result.received, 2);
  assert.equal(result.api, "https://ksef.example");
  assert.equal(JSON.stringify(result).includes("private"), false);
  assert.equal(JSON.stringify(result).includes("secret"), false);
  assert.equal(JSON.stringify(result).includes("password"), false);
});

test("only valid Slack permission labels are displayed; unknown connectors export no config", () => {
  assert.deepEqual(connectionDisplayConfig("slack", { scopes: ["channels:join", "xoxb-private-token"], missing_scopes: ["files:read"], token: "private" }), { scopes: ["channels:join"], missing_scopes: ["files:read"] });
  assert.deepEqual(connectionDisplayConfig("mail", { refresh_token: "private" }), {});
});
