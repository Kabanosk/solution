// Scout agent-task model shared by server queries, preview data and the UI. Pure, no server imports.
export type TaskStatus = "todo" | "in_progress" | "waiting" | "escalated" | "done" | "cancelled";
export type ResolvedVia = "invoicing" | "files" | "email" | "chat_context" | "slack_dm" | "approval" | "auto" | "forecast";

export type Evidence = Record<string, unknown>;

export type AgentTask = {
  id: string;
  loop: string;
  title: string;
  status: TaskStatus;
  ownerId: string | null;
  requestedBy: string;
  resolvedVia: ResolvedVia | null;
  outcome: string | null;
  level: string | null;
  simulated: boolean;
  category: string | null;
  amount: number | null;
  currency: string;
  counterparty: string | null;
  transactionId: string | null;
  evidence: Evidence[];
  /** Approver id of a pending approval, if any. */
  pendingApprovalFor: string | null;
  createdAt: string;
  updatedAt: string;
  closedAt: string | null;
};

export type TaskStep = {
  id: number;
  ts: string;
  kind: string;
  source: string | null;
  resultCount: number | null;
  text: string;
  evidence: Evidence | null;
  /** Only the persisted hiring report is exposed from step.detail; other internal metadata stays private. */
  hiringReport?: string;
};

export type Approval = { id: string; approverId: string; question: string; status: "pending" | "approved" | "rejected"; answeredBy: string | null; answeredAt: string | null; createdAt: string };

export type AuditRow = {
  id: number; ts: string; taskId: string | null; actor: string; requester: string | null; role: string | null;
  action: string; target: string; resource: string; decision: "ALLOW" | "DENY" | "MASK" | "BLOCK"; policy: string | null; reason: string | null;
};

export type Connection = {
  id: string; kind: "bank" | "invoicing" | "mail" | "chat" | "files"; provider: string; enabled: boolean;
  displayName: string; config: Record<string, unknown>; status: "connected" | "error" | "disconnected";
  lastSyncAt: string | null; lastError: string | null;
};

export type CashEvent = { id: number; onDate: string; amount: number; label: string; taskId: string | null };

export type TransactionRow = {
  id: string; bookingDate: string; counterparty: string; reference: string; amount: number;
  category: string | null; invoiceNumber: string | null; documented: boolean;
};

export type TransactionInfo = { id: string; bookingDate: string; type: string; reference: string; counterparty: string; amount: number; cardLast4: string | null };

export type TaskDetail = { task: AgentTask; steps: TaskStep[]; approval: Approval | null; audit: AuditRow[]; transaction: TransactionInfo | null };

/** Everything the dashboard shell needs on first paint. Task evidence, the audit log and the transaction list are
 *  not in here: they load on demand (task detail, /api/records/audit, /api/records/transactions). */
export type ScoutData = {
  ceoId: string;
  tasks: AgentTask[];
  connections: Connection[];
  cashEvents: CashEvent[];
  explained: { documented: number; total: number };
  demo: boolean;
};

export type RecordKind = "audit" | "transactions";
export type ScoutRecords = { audit: AuditRow[]; transactions: TransactionRow[] };

export const STATUS_ORDER: TaskStatus[] = ["in_progress", "waiting", "escalated", "todo", "done", "cancelled"];
export const STATUS_LABEL: Record<TaskStatus, string> = {
  in_progress: "In progress", waiting: "Waiting", escalated: "Escalated", todo: "Todo", done: "Done", cancelled: "Cancelled",
};
export const OPEN_STATUSES: TaskStatus[] = ["todo", "in_progress", "waiting", "escalated"];
export const isOpen = (task: Pick<AgentTask, "status">) => OPEN_STATUSES.includes(task.status);

export const LOOP_LABEL: Record<string, string> = {
  invoicing: "Invoicing", cost_control: "Cost control", liquidity: "Liquidity", chat: "Chat", expenses: "Expenses",
};

export const RESOLVED_VIA_LABEL: Record<ResolvedVia, string> = {
  invoicing: "Invoicing system", files: "Company files", email: "Company email", chat_context: "Chat context",
  slack_dm: "Slack DM", approval: "CEO approval", auto: "Automatic", forecast: "Forecast",
};

export type Channel = "Slack" | "Auto" | "Files" | "Invoicing" | "Chat context" | "Email" | "Forecast" | "Approval";

/** Channel chip: resolved_via first, then the current channel of an open task. */
export function channelChip(task: Pick<AgentTask, "resolvedVia" | "status" | "pendingApprovalFor">): Channel | null {
  switch (task.resolvedVia) {
    case "slack_dm": return "Slack";
    case "auto": return "Auto";
    case "invoicing": return "Invoicing";
    case "files": return "Files";
    case "chat_context": return "Chat context";
    case "email": return "Email";
    case "forecast": return "Forecast";
    case "approval": return "Approval";
  }
  if (task.pendingApprovalFor) return "Approval";
  if (task.status === "waiting" || task.status === "escalated") return "Slack";
  return null;
}

/** Groups in display order; empty groups are omitted. */
export function groupByStatus<T extends Pick<AgentTask, "status" | "updatedAt">>(tasks: T[]): { status: TaskStatus; label: string; tasks: T[] }[] {
  return STATUS_ORDER.map(status => ({
    status, label: STATUS_LABEL[status],
    tasks: tasks.filter(task => task.status === status).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)),
  })).filter(group => group.tasks.length > 0);
}

/** 1 (low) .. 3 (high), shown as priority bars. */
export function priority(task: Pick<AgentTask, "status" | "level" | "amount">): 1 | 2 | 3 {
  if (task.status === "escalated" || task.level === "APPROVAL") return 3;
  if (Math.abs(task.amount ?? 0) >= 1000 || task.level === "NOTIFY") return 2;
  return 1;
}

/** Share with one decimal: 310 of 312 -> "99.4%". */
export function formatShare(part: number, total: number): string {
  if (!total) return "—";
  return `${(Math.round(part * 1000 / total) / 10).toFixed(1)}%`;
}

export function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).map(part => part[0]!.toUpperCase()).slice(0, 2).join("");
}

export type EvidenceView = { kind: "file" | "email" | "chat" | "other"; label: string; detail?: string; href?: string };

/** Normalises evidence JSON written by the backend (file, mail or chat message). */
export function evidenceView(evidence: Evidence): EvidenceView {
  const text = (key: string) => typeof evidence[key] === "string" ? evidence[key] as string : undefined;
  const kind = text("kind") ?? text("type") ?? "";
  const href = text("permalink") ?? text("url") ?? text("href");
  const file = text("filename") ?? text("file") ?? text("name") ?? text("path")?.split("/").pop();
  if (text("subject") || kind === "email" || kind === "mail") {
    return { kind: "email", label: text("subject") ?? file ?? "Email", detail: text("from"), href };
  }
  if (text("quote") || kind === "chat" || kind === "slack" || kind === "chat_context") {
    return { kind: "chat", label: text("quote") ?? text("text") ?? "Chat message", detail: text("author") ?? text("user") ?? text("channel"), href };
  }
  if (file) return { kind: "file", label: file, detail: text("invoice_number") ?? text("location"), href };
  return { kind: "other", label: text("label") ?? text("text") ?? JSON.stringify(evidence), href };
}

const TZ = "Europe/Rome";
/** HH:MM in the company's time zone (stable between server and client render). */
export function clock(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TZ }).format(new Date(iso));
}
export function fullTimestamp(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "medium", timeZone: TZ }).format(new Date(iso)) + " (Rome)";
}
export function shortDate(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: TZ }).format(new Date(iso));
}
export function money(amount: number, currency = "EUR"): string {
  return new Intl.NumberFormat("en-IE", { style: "currency", currency }).format(amount);
}
