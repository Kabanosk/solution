import type { Decision } from "./finance-model.ts";

export type JobStatus = "running" | "queued" | "waiting" | "completed" | "failed" | "rejected" | "attention" | "recorded";
export type JobFilter = "all" | "active" | "needs-attention" | "finished";
export type JobLogEntry = { ts: string; level: "info" | "warning" | "error"; source: string; message: string };

export function parseJobLog(value: unknown): JobLogEntry[] {
  if (!Array.isArray(value)) return [];
  return value.slice(-500).filter((entry): entry is JobLogEntry =>
    entry !== null && typeof entry === "object" && typeof entry.ts === "string" && Number.isFinite(Date.parse(entry.ts)) &&
    ["info", "warning", "error"].includes(entry.level) && typeof entry.source === "string" && typeof entry.message === "string"
  ).map(entry => ({ ts: entry.ts, level: entry.level, source: entry.source.slice(0, 80), message: entry.message.slice(0, 2000) }));
}
export type JobApproval = { id: string; status: string; approver: string; question: string; createdAt: string; resolvedAt: string | null };
export type JobUsage = { calls: number; failedCalls: number; latencyMs: number; tokens: number; estimated: boolean; events: { id: string; kind: string | null }[] };
export type AgentJob = {
  id: string;
  title: string;
  reason: string;
  action: string;
  actor: string;
  level: string;
  status: JobStatus;
  recordedAt: string;
  simulated: boolean;
  approval: JobApproval | null;
  usage: JobUsage;
};
export type DecisionJobRecord = Decision & {
  action_type: string;
  actor: string;
  approval: JobApproval | null;
  usage: JobUsage;
};

export const jobStatusLabels: Record<JobStatus, string> = {
  running: "Running", queued: "Queued", waiting: "Waiting for approval", completed: "Completed",
  failed: "Failed", rejected: "Rejected", attention: "Needs attention", recorded: "Recorded",
};

export function jobReference(job: AgentJob): string {
  return `JOB-${job.id.replace(/^(dec[_-]|preview-)/, "").slice(0, 8).toUpperCase()}`;
}

// Decisions are outcomes, not a run queue. Never infer running/queued from an
// event without an outcome, or treat a failed model retry as a failed job.
export function jobFromDecision(row: DecisionJobRecord): AgentJob {
  let status: JobStatus = "recorded";
  if (row.approval?.status === "pending" || (row.status === "pending" && row.level === "APPROVAL" && !row.approval)) status = "waiting";
  else if (row.status === "rejected") status = "rejected";
  else if (row.status === "executed" || row.status === "completed") status = "completed";
  else if (row.status === "failed" || row.status === "error") status = "failed";
  else if (row.status === "escalated" || row.status === "pending") status = "attention";
  return {
    id: row.id, title: row.summary, reason: row.reason, action: row.action_type, actor: row.actor,
    level: row.level, status, recordedAt: row.ts, simulated: row.executed_simulated,
    approval: row.approval, usage: row.usage,
  };
}

export function filterJobs(jobs: AgentJob[], status: JobStatus | JobFilter, query: string): AgentJob[] {
  const term = query.trim().toLowerCase();
  return jobs.filter(job => (status === "all" || job.status === status ||
    (status === "active" && ["queued", "running"].includes(job.status)) ||
    (status === "needs-attention" && ["waiting", "attention", "failed"].includes(job.status)) ||
    (status === "finished" && ["completed", "rejected"].includes(job.status))) &&
    `${job.id} ${jobReference(job)} ${job.title} ${job.action} ${job.actor} ${job.usage.events.map(event => event.id).join(" ")}`.toLowerCase().includes(term));
}

export function formatModelTime(milliseconds: number): string {
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)} s`;
  return `${(milliseconds / 60000).toFixed(1)} min`;
}
