import { test } from "node:test";
import assert from "node:assert/strict";
import { activeItem, GO_SHORTCUTS, hashFromRoute, NAV, NAV_ITEMS, routeFromHash } from "../lib/nav.ts";

test("sidebar sections, order and hrefs: Overview first, one task list, no separate Forecast", () => {
  assert.deepEqual(NAV.map(section => section.section), ["primary", "Loops", "Finance", "Workspace"]);
  assert.deepEqual(NAV.map(section => section.items.map(item => item.label)), [
    ["Overview", "Inbox", "Tasks", "Scout chat"],
    ["Invoicing", "Cost control", "Liquidity", "Chat"],
    ["Transactions", "Staffing"],
    ["Connections", "Audit log", "Access policies", "LLM usage"],
  ]);
  assert.deepEqual(NAV_ITEMS.map(item => item.href), [
    "/#overview", "/#tasks?scope=inbox", "/#tasks", "/#chat",
    "/#tasks?loop=invoicing", "/#tasks?loop=cost_control", "/#tasks?loop=liquidity", "/#tasks?loop=chat",
    "/#transactions", "/#staffing",
    "/#connections", "/#audit", "/#policies", "/#llm-usage",
  ]);
  assert.deepEqual(NAV[1].items.map(item => item.loop), ["invoicing", "cost_control", "liquidity", "chat"]);
  assert.equal(NAV[0].collapsible, false);
  assert.ok(NAV.slice(1).every(section => section.collapsible));
  assert.equal(new Set(NAV_ITEMS.map(item => item.id)).size, NAV_ITEMS.length);
});

test("hash routing, legacy hashes and g-shortcuts", () => {
  assert.deepEqual(routeFromHash(""), { view: "overview" });
  assert.deepEqual(routeFromHash("#forecast"), { view: "overview" });
  assert.deepEqual(routeFromHash("#tasks"), { view: "tasks", filter: {} });
  assert.deepEqual(routeFromHash("#tasks?loop=invoicing&status=done"), { view: "tasks", filter: { loop: "invoicing", status: "done" } });
  assert.deepEqual(routeFromHash("#inbox"), { view: "tasks", filter: { scope: "inbox" } });
  assert.deepEqual(routeFromHash("#my-tasks"), { view: "tasks", filter: { scope: "mine" } });
  assert.deepEqual(routeFromHash("#loop-cost-control"), { view: "tasks", filter: { loop: "cost_control" } });
  assert.deepEqual(routeFromHash("#task/SC-110"), { view: "tasks", taskId: "SC-110" });
  assert.deepEqual(routeFromHash("#investigations"), { view: "tasks" });
  assert.deepEqual(routeFromHash("#agent-jobs/abc"), { view: "tasks" });
  assert.deepEqual(routeFromHash("#llm"), { view: "chat" });
  assert.deepEqual(routeFromHash("#nope"), { view: "overview" });
  assert.equal(hashFromRoute({ view: "tasks", filter: { scope: "mine", loop: "chat" } }), "#tasks?scope=mine&loop=chat");
  assert.equal(hashFromRoute({ view: "tasks", taskId: "SC 1" }), "#task/SC%201");
  assert.deepEqual(GO_SHORTCUTS.t, { view: "tasks" });
  assert.deepEqual(GO_SHORTCUTS.i, { view: "tasks", filter: { scope: "inbox" } });
});

test("the active sidebar item follows the task-list filter", () => {
  assert.equal(activeItem({ view: "overview" })?.id, "overview");
  assert.equal(activeItem({ view: "tasks", filter: {} })?.id, "tasks");
  assert.equal(activeItem({ view: "tasks", filter: { loop: "liquidity", status: "done" } })?.id, "loop-liquidity");
  assert.equal(activeItem({ view: "tasks", filter: { scope: "inbox" } })?.id, "inbox");
  assert.equal(activeItem({ view: "tasks", filter: { scope: "mine" } })?.id, "tasks");
});
