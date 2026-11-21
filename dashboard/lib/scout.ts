import "server-only";
import type { PoolClient } from "pg";
import { displayCents } from "./finance-model";
import { connectionDisplayConfig } from "./connection-display";
import type { AgentTask, AuditRow, CashEvent, Connection, ScoutData, ScoutRecords, TaskDetail, TaskStep, TransactionInfo, TransactionRow } from "./scout-model";

// Read-only queries over the Scout tables (backend/scout/schema.sql). Timestamps are rendered as UTC ISO strings
// and money as integer cents in SQL, so no float arithmetic happens here.
const iso = (column: string) => `to_char(${column} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')`;
const cents = (column: string) => `(${column} * 100)::bigint::text`;

export const ceoId = () => process.env.SCOUT_CEO_ID ?? "vincenzo";
export const demoEnabled = () => process.env.SCOUT_DEMO === "1";

const TASK_COLUMNS = `t.id, t.loop, t.title, t.status, t.owner_id, t.requested_by, t.resolved_via, t.outcome, t.level,
  t.simulated, t.category, ${cents("t.amount")} AS amount, t.currency, t.counterparty, t.transaction_id, t.evidence,
  ${iso("t.created_at")} AS created_at, ${iso("t.updated_at")} AS updated_at, ${iso("t.closed_at")} AS closed_at,
  (SELECT a.approver_id FROM approvals a WHERE a.task_id = t.id AND a.status = 'pending' ORDER BY a.created_at DESC LIMIT 1) AS pending_approval_for`;

type Row = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

function task(row: Row): AgentTask {
  return {
    id: row.id, loop: row.loop, title: row.title, status: row.status, ownerId: row.owner_id, requestedBy: row.requested_by,
    resolvedVia: row.resolved_via, outcome: row.outcome, level: row.level, simulated: row.simulated, category: row.category,
    amount: row.amount === null ? null : displayCents(row.amount), currency: row.currency, counterparty: row.counterparty,
    transactionId: row.transaction_id, evidence: Array.isArray(row.evidence) ? row.evidence : [],
    pendingApprovalFor: row.pending_approval_for, createdAt: row.created_at, updatedAt: row.updated_at, closedAt: row.closed_at,
  };
}

const AUDIT_COLUMNS = `id::int AS id, ${iso("ts")} AS ts, task_id, actor, requester, role, action, target, resource, decision, policy, reason`;
const audit = (row: Row): AuditRow => ({
  id: row.id, ts: row.ts, taskId: row.task_id, actor: row.actor, requester: row.requester, role: row.role, action: row.action,
  target: row.target, resource: row.resource, decision: row.decision, policy: row.policy, reason: row.reason,
});

export async function loadScout(client: PoolClient): Promise<ScoutData> {
  // The list needs no evidence (the task detail loads it): it was a third of the page payload.
  const tasks = (await client.query(`SELECT ${TASK_COLUMNS.replace("t.evidence", "'[]'::jsonb AS evidence")} FROM agent_tasks t
    ORDER BY t.updated_at DESC LIMIT 300`)).rows.map(task);
  const connections: Connection[] = (await client.query(`SELECT id, kind, provider, enabled, display_name, config, status,
      ${iso("last_sync_at")} AS last_sync_at, last_error FROM connections ORDER BY id`)).rows.map(row => ({
    id: row.id, kind: row.kind, provider: row.provider, enabled: row.enabled, displayName: row.display_name,
    config: connectionDisplayConfig(row.id, row.config), status: row.status, lastSyncAt: row.last_sync_at, lastError: row.last_error,
  }));
  const cashEvents: CashEvent[] = (await client.query(`SELECT id::int AS id, to_char(on_date, 'YYYY-MM-DD') AS on_date,
      ${cents("amount")} AS amount, label, task_id FROM expected_cash_events
      WHERE status = 'expected' AND on_date >= current_date ORDER BY on_date, id LIMIT 20`)).rows.map(row => ({
    id: row.id, onDate: row.on_date, amount: displayCents(row.amount), label: row.label, taskId: row.task_id,
  }));
  const explained = (await client.query(`SELECT
      count(*) FILTER (WHERE documented)::int AS documented, count(*)::int AS total
      FROM transactions WHERE amount < 0 AND invoice_number IS NOT NULL`)).rows[0];
  return { ceoId: ceoId(), tasks, connections, cashEvents, explained, demo: demoEnabled() };
}

/** The audit log or the latest bank bookings, loaded when their view opens. */
export async function loadRecords<K extends keyof ScoutRecords>(client: PoolClient, kind: K): Promise<ScoutRecords[K]> {
  if (kind === "audit") {
    return (await client.query(`SELECT ${AUDIT_COLUMNS} FROM audit_log ORDER BY ts DESC, id DESC LIMIT 300`)).rows.map(audit) as ScoutRecords[K];
  }
  const transactions: TransactionRow[] = (await client.query(`SELECT id, to_char(booking_date, 'YYYY-MM-DD') AS booking_date,
      counterparty, reference, ${cents("amount")} AS amount, category, invoice_number, documented
      FROM transactions ORDER BY booking_date DESC, id DESC LIMIT 100`)).rows.map(row => ({
    id: row.id, bookingDate: row.booking_date, counterparty: row.counterparty, reference: row.reference,
    amount: displayCents(row.amount), category: row.category, invoiceNumber: row.invoice_number, documented: row.documented,
  }));
  return transactions as ScoutRecords[K];
}

export async function loadTaskDetail(client: PoolClient, id: string): Promise<TaskDetail | null> {
  const row = (await client.query(`SELECT ${TASK_COLUMNS} FROM agent_tasks t WHERE t.id = $1`, [id])).rows[0];
  if (!row) return null;
  const steps: TaskStep[] = (await client.query(`SELECT id::int AS id, ${iso("ts")} AS ts, kind, source, result_count, text, evidence
      FROM agent_task_steps WHERE task_id = $1 ORDER BY id`, [id])).rows.map(step => ({
    id: step.id, ts: step.ts, kind: step.kind, source: step.source, resultCount: step.result_count, text: step.text, evidence: step.evidence,
  }));
  const approvalRow = (await client.query(`SELECT id, approver_id, question, status, answered_by, ${iso("answered_at")} AS answered_at,
      ${iso("created_at")} AS created_at FROM approvals WHERE task_id = $1 ORDER BY created_at DESC LIMIT 1`, [id])).rows[0];
  const auditRows = (await client.query(`SELECT ${AUDIT_COLUMNS} FROM audit_log WHERE task_id = $1 ORDER BY ts, id`, [id])).rows.map(audit);
  let transaction: TransactionInfo | null = null;
  if (row.transaction_id) {
    const tx = (await client.query(`SELECT t.id, to_char(t.booking_date, 'YYYY-MM-DD') AS booking_date, t.type, t.reference,
        t.counterparty, ${cents("t.amount")} AS amount, b.card_last4
        FROM transactions t LEFT JOIN bank_mock_transactions b ON b.id = t.id WHERE t.id = $1`, [row.transaction_id])).rows[0];
    if (tx) transaction = { id: tx.id, bookingDate: tx.booking_date, type: tx.type, reference: tx.reference,
      counterparty: tx.counterparty, amount: displayCents(tx.amount), cardLast4: tx.card_last4 };
  }
  return {
    task: task(row), steps, audit: auditRows, transaction,
    approval: approvalRow ? { id: approvalRow.id, approverId: approvalRow.approver_id, question: approvalRow.question, status: approvalRow.status,
      answeredBy: approvalRow.answered_by, answeredAt: approvalRow.answered_at, createdAt: approvalRow.created_at } : null,
  };
}
