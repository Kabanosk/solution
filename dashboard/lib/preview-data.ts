import "server-only";
import { financePeriod, type DashboardData } from "./finance-model";
import { jobFromDecision, type AgentJob, type JobLogEntry, type JobStatus } from "./agent-jobs";

// Invented UI fixtures, deliberately independent of private company records.
export function getPreviewData(): DashboardData {
  const period = (scale: number) => financePeriod([
    { category: "revenue", count: "120", income: String(12000000 * scale), expense: "0" },
    { category: "payroll", count: "12", income: "0", expense: String(3600000 * scale) },
    { category: "food", count: "24", income: "0", expense: String(2400000 * scale) },
    { category: "rent", count: "12", income: "0", expense: String(1200000 * scale) },
    { category: "utilities", count: "12", income: "0", expense: String(600000 * scale) },
    { category: "tax", count: "4", income: "0", expense: String(400000 * scale) },
  ]);
  return {
    asOf: "2026-11-20", yearLabel: "2026", year: period(1), q4: period(0.25),
    closingBalance: 38000, monthlyPayroll: 9000, monthlyContributions: 1800,
    undocumentedPayments: 3, pendingApprovals: 1,
    employees: [
      { id: "preview-1", name: "Alex Morgan", initials: "AM", role: "Operations lead", team: "Operations", salary: 3200, avatar: "avatar-green" },
      { id: "preview-2", name: "Sam Taylor", initials: "ST", role: "Team coordinator", team: "Operations", salary: 2800, avatar: "avatar-orange" },
      { id: "preview-3", name: "Jordan Lee", initials: "JL", role: "Finance specialist", team: "Finance", salary: 3000, avatar: "avatar-green" },
      { id: "preview-4", name: "Robin Clark", initials: "RC", role: "New starter", team: "Operations", salary: null, avatar: "avatar-green" },
    ],
    decisions: [
      { id: "preview-approval", ts: "2026-11-20T10:30:00Z", summary: "Review the proposed supplier payment", reason: "This example payment needs an owner’s approval because it exceeds the routine spending threshold.", level: "APPROVAL", status: "pending", executed_simulated: false },
      { id: "preview-document", ts: "2026-11-20T09:15:00Z", summary: "Invoice document matched to a bank booking", reason: "In this sample investigation, the invoice reference and amount match the recorded payment.", level: "AUTO", status: "completed", executed_simulated: true },
    ],
    jobs: previewJobs(),
    forecast: {
      asOf: "2026-11-20", model: "Sample forecast", safetyBuffer: 18000,
      points: Array.from({ length: 91 }, (_, day) => ({ day, balance: Math.round(38000 - day * 110 + Math.sin(day / 8) * 2300) })),
    },
  };
}

function previewJobs(): AgentJob[] {
  const samples: { id: string; title: string; action: string; status: JobStatus; reason: string; level?: string; simulated?: boolean }[] = [
    { id: "preview-running", title: "Match supplier invoices to bank payments", action: "attach_invoice", status: "running", reason: "The agent is checking sample invoice references and totals against recorded payments." },
    { id: "preview-approval", title: "Review the proposed supplier payment", action: "supplier_payment", status: "waiting", level: "APPROVAL", reason: "This example payment exceeds the routine spending threshold and needs an owner’s approval." },
    { id: "preview-queued", title: "Prepare the monthly cost review", action: "cost_review", status: "queued", reason: "This sample job is queued for the next agent cycle." },
    { id: "preview-document", title: "Invoice document matched to a bank booking", action: "attach_invoice", status: "completed", reason: "The sample invoice reference and amount match the recorded payment." },
    { id: "preview-forecast", title: "Update the 90-day cash forecast", action: "liquidity_forecast", status: "completed", simulated: true, reason: "The sample forecast includes the latest bookings and scheduled costs. This example makes no real bank action." },
    { id: "preview-failed", title: "Retrieve a missing supplier document", action: "find_invoice", status: "failed", reason: "The example document service was unavailable. The document was not retrieved." },
    { id: "preview-rejected", title: "Increase the marketing budget", action: "budget_change", status: "rejected", level: "APPROVAL", reason: "The owner rejected this example budget increase while acquisition costs are above target." },
    { id: "preview-escalated", title: "Resolve an invoice amount mismatch", action: "invoice_mismatch", status: "attention", reason: "The sample invoice total differs from the payment. The agent has asked the responsible person to explain the difference." },
  ];
  return samples.map((sample, index) => {
    const recordedAt = `2026-11-20T${String(12 - Math.floor(index / 2)).padStart(2, "0")}:${index % 2 ? "15" : "45"}:00Z`;
    const status = sample.status === "waiting" ? "pending" : sample.status === "attention" ? "escalated" : sample.status;
    const hasUsage = !["queued", "failed"].includes(sample.status);
    const job = jobFromDecision({
      id: sample.id, ts: recordedAt, summary: sample.title, reason: sample.reason, action_type: sample.action,
      actor: "CFO agent", level: sample.level ?? "AUTO", status, executed_simulated: sample.simulated ?? false,
      approval: sample.level === "APPROVAL" ? {
        id: `approval-${sample.id}`, status: sample.status === "rejected" ? "rejected" : "pending", approver: "Workspace owner",
        question: "May I proceed with this proposed action?", createdAt: recordedAt,
        resolvedAt: sample.status === "rejected" ? "2026-11-20T09:20:00Z" : null,
      } : null,
      usage: { calls: hasUsage ? 2 : 0, failedCalls: 0, latencyMs: hasUsage ? 2400 + index * 870 : 0,
        tokens: hasUsage ? 1520 + index * 210 : 0, estimated: false,
        events: [{ id: `event-${sample.id}`, kind: sample.action === "attach_invoice" ? "bank_transaction" : sample.action }] },
    });
    if (sample.status === "running" || sample.status === "queued") {
      job.status = sample.status;
    }
    return job;
  });
}

export function getPreviewJobLog(id: string): JobLogEntry[] | null {
  const job = previewJobs().find(job => job.id === id);
  if (!job) return null;
  const messages: { source: string; message: string; level?: JobLogEntry["level"] }[] = job.status === "queued"
    ? [{ source: "queue", message: "Queued for the next agent cycle." }]
    : [
      { source: "load_records", message: "Loaded sample records for this task." },
      { source: "check_evidence", message: "Checked the available references and amounts." },
      { source: "policy", message: job.level === "APPROVAL" ? "This action requires the workspace owner’s approval." : "Checked the action against the workspace rules." },
      { source: job.status === "waiting" ? "request_approval" : "outcome", message:
        job.status === "waiting" ? "Waiting for the workspace owner’s approval."
        : job.status === "running" ? "Looking for additional matching documents."
        : job.status === "failed" ? "Document service unavailable. The document was not retrieved."
        : job.status === "rejected" ? "The workspace owner rejected the proposed action."
        : job.status === "attention" ? "Asked the responsible person to explain the mismatch."
        : job.simulated ? "Completed. Execution was simulated." : "Action completed.",
        level: job.status === "failed" ? "error" : job.status === "attention" || job.status === "rejected" ? "warning" : "info" },
    ];
  const start = Date.parse(job.recordedAt);
  return messages.map((entry, index) => ({ ...entry, level: entry.level ?? "info", ts: new Date(start + index * 1200).toISOString() }));
}
