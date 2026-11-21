import { test } from "node:test";
import assert from "node:assert/strict";
import { channelChip, evidenceView, formatShare, groupByStatus, type AgentTask, type ResolvedVia, type TaskStatus } from "../lib/scout-model.ts";

const chip = (resolvedVia: ResolvedVia | null, status: TaskStatus = "done", pendingApprovalFor: string | null = null) =>
  channelChip({ resolvedVia, status, pendingApprovalFor });

test("channel chip mapping", () => {
  assert.equal(chip("slack_dm"), "Slack");
  assert.equal(chip("auto"), "Auto");
  assert.equal(chip("invoicing"), "Invoicing");
  assert.equal(chip("files"), "Files");
  assert.equal(chip("chat_context"), "Chat context");
  assert.equal(chip("email"), "Email");
  assert.equal(chip("forecast"), "Forecast");
  assert.equal(chip("approval"), "Approval");
  assert.equal(chip(null, "waiting"), "Slack");
  assert.equal(chip(null, "escalated"), "Slack");
  assert.equal(chip(null, "waiting", "vincenzo"), "Approval");
  assert.equal(chip(null, "in_progress"), null);
  assert.equal(chip(null, "todo"), null);
});

test("status grouping order", () => {
  const statuses: TaskStatus[] = ["done", "todo", "cancelled", "escalated", "waiting", "in_progress", "done"];
  const tasks = statuses.map((status, index) => ({ status, updatedAt: `2026-11-20T10:0${index}:00Z` }) as Pick<AgentTask, "status" | "updatedAt">);
  const groups = groupByStatus(tasks);
  assert.deepEqual(groups.map(group => group.label), ["In progress", "Waiting", "Escalated", "Todo", "Done", "Cancelled"]);
  assert.equal(groups[4].tasks.length, 2);
  assert.equal(groups[4].tasks[0].updatedAt, "2026-11-20T10:06:00Z", "newest first");
  assert.deepEqual(groupByStatus(tasks.filter(task => task.status !== "todo")).map(group => group.status),
    ["in_progress", "waiting", "escalated", "done", "cancelled"]);
});

test("explained share formats 310/312 as 99.4%", () => {
  assert.equal(formatShare(310, 312), "99.4%");
  assert.equal(formatShare(312, 312), "100.0%");
  assert.equal(formatShare(0, 0), "—");
});

test("evidence is normalised for files, mail and chat", () => {
  assert.deepEqual(evidenceView({ kind: "file", filename: "FLV-42026.pdf" }), { kind: "file", label: "FLV-42026.pdf", detail: undefined, href: undefined });
  assert.equal(evidenceView({ subject: "Invoice CWM-8812" }).kind, "email");
  const chat = evidenceView({ kind: "chat", quote: "approved", permalink: "https://slack.example/p1" });
  assert.equal(chat.kind, "chat");
  assert.equal(chat.href, "https://slack.example/p1");
});
