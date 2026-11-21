import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

test("deployment archive includes the scheduler entry point and excludes secrets and build output", () => {
  const directory = mkdtempSync(join(tmpdir(), "scout-package-test-"));
  const archive = join(directory, "source.tar.gz");
  try {
    execFileSync("python3", [fileURLToPath(new URL("../scripts/package-source.py", import.meta.url)), archive]);
    const names: string[] = JSON.parse(execFileSync("python3", ["-c", "import json,sys,tarfile; print(json.dumps(tarfile.open(sys.argv[1]).getnames()))", archive], { encoding: "utf8" }));
    assert.ok(names.includes("instrumentation.ts"));
    assert.ok(names.includes("lib/strategy-store.ts"));
    assert.ok(names.includes("app/api/strategy/settings/route.ts"));
    assert.ok(!names.some(name => /(?:^|\/)(?:\.env(?:\.|$)|node_modules(?:\/|$)|\.next(?:\/|$)|data(?:\/|$))/.test(name)));
  } finally { rmSync(directory, { recursive: true, force: true }); }
});
