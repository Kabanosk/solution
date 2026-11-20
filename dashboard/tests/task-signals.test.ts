import { test } from "node:test";
import assert from "node:assert/strict";
import { baselineSeen, filterTasks, loopSignal, unseenResult } from "../lib/task-signals.ts";
import type { AgentTask, TaskStatus } from "../lib/scout-model.ts";

const task = (id: string, loop: string, status: TaskStatus, extra: Partial<AgentTask> = {}) => ({
  id, loop, status, ownerId: null, pendingApprovalFor: null, updatedAt: "2026-11-20T10:00:00Z",
  closedAt: status === "done" ? "2026-11-20T10:00:00Z" : null, ...extra,
});

test("loop dot: none, running (blue), unseen result (green), needs reaction (amber)", () => {
  assert.equal(loopSignal([], "invoicing", {}).signal, null);
  assert.equal(loopSignal([task("a", "invoicing", "in_progress")], "invoicing", {}).signal, "running");
  assert.equal(loopSignal([task("a", "invoicing", "todo")], "invoicing", {}).signal, null);
  const done = task("b", "invoicing", "done");
  assert.equal(loopSignal([done], "invoicing", {}).signal, "result");
  assert.equal(loopSignal([done], "invoicing", { b: "2026-11-20T10:00:00Z" }).signal, null, "opened after it finished");
  assert.equal(loopSignal([done, task("c", "invoicing", "waiting")], "invoicing", {}).signal, "attention");
  assert.equal(loopSignal([task("d", "invoicing", "in_progress", { pendingApprovalFor: "vincenzo" })], "invoicing", {}).signal, "attention");
  assert.equal(loopSignal([task("e", "liquidity", "escalated")], "invoicing", {}).signal, null, "other loops do not count");
});

test("a result opened while still running lights up again once it finishes", () => {
  const finished = task("a", "chat", "done", { closedAt: "2026-11-20T12:00:00Z", updatedAt: "2026-11-20T12:00:00Z" });
  assert.equal(unseenResult(finished, { a: "2026-11-20T11:00:00Z" }), true);
  assert.equal(unseenResult(finished, baselineSeen([finished])), false);
});

test("task list filters: scope, loop, status, owner", () => {
  const tasks = [
    task("1", "invoicing", "waiting", { ownerId: "vincenzo" }),
    task("2", "invoicing", "done", { ownerId: "grazyna" }),
    task("3", "liquidity", "escalated", { pendingApprovalFor: "vincenzo" }),
    task("4", "chat", "in_progress", { ownerId: "vincenzo" }),
  ];
  const ids = (filter: Parameters<typeof filterTasks>[1]) => filterTasks(tasks, filter, "vincenzo").map(item => item.id);
  assert.deepEqual(ids({}), ["1", "2", "3", "4"]);
  assert.deepEqual(ids({ scope: "inbox" }), ["1", "3"]);
  assert.deepEqual(ids({ scope: "mine" }), ["1", "4"]);
  assert.deepEqual(ids({ loop: "invoicing", status: "done" }), ["2"]);
  assert.deepEqual(ids({ owner: "grazyna" }), ["2"]);
});
