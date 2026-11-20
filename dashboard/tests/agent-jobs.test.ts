import { test } from "node:test";
import assert from "node:assert/strict";
import { filterJobs, jobFromDecision, parseJobLog, type DecisionJobRecord } from "../lib/agent-jobs.ts";

const decision = (changes: Partial<DecisionJobRecord> = {}): DecisionJobRecord => ({
  id: "dec-invoice", ts: "2026-11-20T12:00:00Z", summary: "Match invoice INV-101",
  reason: "Invoice and payment amounts match", level: "AUTO", status: "executed",
  executed_simulated: false, action_type: "attach_invoice", actor: "agent", approval: null,
  usage: { calls: 2, failedCalls: 0, latencyMs: 2400, tokens: 1800, estimated: false, events: [{ id: "event-101", kind: "bank_transaction" }] },
  ...changes,
});

test("job logs tolerate legacy payloads and reject malformed entries", () => {
  const valid = { ts: "2026-11-20T12:00:00Z", level: "info", source: "attach", message: "Invoice attached" };
  assert.deepEqual(parseJobLog(null), []);
  assert.deepEqual(parseJobLog({}), []);
  assert.deepEqual(parseJobLog([valid, null, { ...valid, ts: "invalid" }, { ...valid, level: "invalid" }, { ...valid, message: 42 }]), [valid]);
  assert.equal(parseJobLog(Array.from({ length: 510 }, () => valid)).length, 500);
  assert.equal(parseJobLog([{ ...valid, message: "x".repeat(2100) }])[0].message.length, 2000);
});

test("pending approvals and resolved outcomes retain different states", () => {
  const approval = { id: "apr-1", approver: "ceo", question: "Proceed?", status: "pending", createdAt: "2026-11-20T12:00:00Z", resolvedAt: null };
  const waiting = jobFromDecision(decision({ level: "APPROVAL", status: "pending", approval }));
  assert.equal(waiting.status, "waiting");
  const approved = jobFromDecision(decision({ level: "APPROVAL", approval: { ...approval, status: "approved", resolvedAt: "2026-11-20T12:10:00Z" }, executed_simulated: true }));
  assert.equal(approved.status, "completed");
  assert.equal(approved.simulated, true);
  const rejected = jobFromDecision(decision({ level: "APPROVAL", status: "rejected", approval: { ...approval, status: "rejected" } }));
  assert.equal(rejected.status, "rejected");
});

test("missing telemetry never invents an active run, elapsed time or model call", () => {
  const job = jobFromDecision(decision({ usage: { calls: 0, failedCalls: 0, latencyMs: 0, tokens: 0, estimated: false, events: [] } }));
  assert.equal(job.status, "completed");
  assert.equal(job.usage.latencyMs, 0);
  for (const status of ["pending", "new-status", "running", "queued"]) {
    const result = jobFromDecision(decision({ status }));
    assert.ok(!["running", "queued", "completed"].includes(result.status));
  }
  assert.equal(jobFromDecision(decision({ status: "escalated" })).status, "attention");
});

test("a failed model call does not override a completed action", () => {
  const job = jobFromDecision(decision({ usage: { calls: 2, failedCalls: 2, latencyMs: 1000, tokens: 100, estimated: true, events: [] } }));
  assert.equal(job.status, "completed");
  assert.equal(job.usage.failedCalls, 2);
});

test("search and status filters combine without changing source order", () => {
  const completed = jobFromDecision(decision());
  const waiting = jobFromDecision(decision({ id: "dec-approval", level: "APPROVAL", status: "pending" }));
  const jobs = [waiting, completed];
  assert.deepEqual(filterJobs(jobs, "waiting", " inv-101 "), [waiting]);
  assert.deepEqual(filterJobs(jobs, "completed", "event-101"), [completed]);
  assert.deepEqual(filterJobs(jobs, "all", "DEC-APPROVAL"), [waiting]);
  assert.deepEqual(filterJobs(jobs, "all", "JOB-INVOICE"), [completed]);
  assert.deepEqual(filterJobs(jobs, "failed", ""), []);
  assert.deepEqual(filterJobs(jobs, "all", ""), [waiting, completed]);
  assert.deepEqual(filterJobs(jobs, "needs-attention", ""), [waiting]);
  assert.deepEqual(filterJobs(jobs, "finished", ""), [completed]);
  assert.deepEqual(filterJobs(jobs, "active", ""), []);
});
