import { test } from "node:test";
import assert from "node:assert/strict";
import { readAuthBody } from "../lib/auth-request.ts";
import { clientAddress } from "../lib/auth-client-address.ts";

test("auth bodies reject oversized streams before buffering the entire body", async () => {
  let reads = 0;
  const body = new ReadableStream({ pull(controller) { reads++; controller.enqueue(new Uint8Array(4096)); if (reads >= 10) controller.close(); } });
  const request = new Request("https://scout.example/api/auth/login", { method: "POST", body, duplex: "half" } as RequestInit);
  await assert.rejects(readAuthBody(request), /large/);
  assert.ok(reads < 10);
});
test("auth bodies allow only a JSON object", async () => {
  const request = (body: string) => new Request("https://scout.example", { method: "POST", body });
  assert.deepEqual(await readAuthBody(request('{"email":"owner@example.com"}')), { email: "owner@example.com" });
  for (const body of ["[]", "null", "oops"]) await assert.rejects(readAuthBody(request(body)));
});
test("client address cannot trust arbitrary forwarded headers without explicit deployment configuration", () => {
  const headers = new Headers({ "x-forwarded-for": "192.0.2.1", "x-scout-client-ip": "198.51.100.2" });
  assert.throws(() => clientAddress(headers, "", true));
  assert.equal(clientAddress(headers, "", false), "local-development");
  assert.equal(clientAddress(headers, "x-scout-client-ip", true), "198.51.100.2");
  assert.throws(() => clientAddress(new Headers({ "x-scout-client-ip": "1.1.1.1, 2.2.2.2" }), "x-scout-client-ip", true));
});
