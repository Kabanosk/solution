import { test } from "node:test";
import assert from "node:assert/strict";
import { legalLinks } from "../lib/legal-links.ts";

test("login footer links to built-in public legal pages without external configuration", () => {
  const privacy = process.env.SCOUT_PRIVACY_URL, terms = process.env.SCOUT_TERMS_URL;
  try {
    delete process.env.SCOUT_PRIVACY_URL;
    delete process.env.SCOUT_TERMS_URL;
    assert.deepEqual(legalLinks(), { privacy: "/privacy", terms: "/terms" });
    process.env.SCOUT_PRIVACY_URL = "http://untrusted.example/privacy";
    process.env.SCOUT_TERMS_URL = "javascript:alert(1)";
    assert.deepEqual(legalLinks(), { privacy: "/privacy", terms: "/terms" });
    process.env.SCOUT_PRIVACY_URL = "https://scout.example/privacy";
    process.env.SCOUT_TERMS_URL = "https://scout.example/terms";
    assert.deepEqual(legalLinks(), { privacy: "https://scout.example/privacy", terms: "https://scout.example/terms" });
  } finally {
    if (privacy === undefined) delete process.env.SCOUT_PRIVACY_URL;
    else process.env.SCOUT_PRIVACY_URL = privacy;
    if (terms === undefined) delete process.env.SCOUT_TERMS_URL;
    else process.env.SCOUT_TERMS_URL = terms;
  }
});
