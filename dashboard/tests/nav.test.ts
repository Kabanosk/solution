import { test } from "node:test";
import assert from "node:assert/strict";
import { GO_SHORTCUTS, NAV, NAV_ITEMS, viewFromHash } from "../lib/nav.ts";

test("sidebar sections, order and hrefs match the Scout spec", () => {
  assert.deepEqual(NAV.map(section => section.section), ["primary", "Loops", "Finance", "Workspace"]);
  assert.deepEqual(NAV.map(section => section.items.map(item => item.label)), [
    ["Inbox", "My tasks", "Agent tasks"],
    ["Invoicing", "Cost control", "Liquidity", "Chat"],
    ["Overview", "Transactions", "Forecast", "Staffing"],
    ["Connections", "Audit log", "Access policies"],
  ]);
  assert.deepEqual(NAV_ITEMS.map(item => item.href), [
    "/#inbox", "/#my-tasks", "/#tasks",
    "/#loop-invoicing", "/#loop-cost-control", "/#loop-liquidity", "/#loop-chat",
    "/#overview", "/#transactions", "/#forecast", "/#staffing",
    "/#connections", "/#audit", "/#policies",
  ]);
  assert.deepEqual(NAV[1].items.map(item => item.loop), ["invoicing", "cost_control", "liquidity", "chat"]);
  assert.equal(NAV[0].collapsible, false);
  assert.ok(NAV.slice(1).every(section => section.collapsible));
  assert.equal(new Set(NAV_ITEMS.map(item => item.id)).size, NAV_ITEMS.length);
});

test("hash routing and g-shortcuts", () => {
  assert.equal(viewFromHash(""), "overview");
  assert.equal(viewFromHash("#tasks"), "tasks");
  assert.equal(viewFromHash("#investigations"), "tasks");
  assert.equal(viewFromHash("#nope"), "overview");
  assert.equal(GO_SHORTCUTS.t, "tasks");
  assert.equal(GO_SHORTCUTS.i, "inbox");
});
