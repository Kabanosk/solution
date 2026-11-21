import "server-only";
import { financePeriod, quarterSpend, type DashboardData, type Forecast } from "./finance-model";
import type { AgentTask, ScoutRecords, Approval, AuditRow, ResolvedVia, TaskDetail, TaskStatus, TaskStep, TransactionInfo } from "./scout-model";

// Invented UI fixtures for Pizzeria Amalfi (deck wording), deliberately independent of private company records.
type Fixture = {
  task: Partial<AgentTask> & { id: string; loop: string; title: string; status: TaskStatus };
  steps: [string, string, string | null, number | null, string, Record<string, unknown>?][]; // HH:MM, kind, source, count, text, evidence
  approval?: Omit<Approval, "createdAt">;
  transaction?: TransactionInfo;
};

const DAY = "2026-11-20";
const at = (hhmm: string, day = DAY) => `${day}T${String(Number(hhmm.slice(0, 2)) - 1).padStart(2, "0")}:${hhmm.slice(3)}:00Z`; // Rome = UTC+1

const fixtures: Fixture[] = [
  {
    task: { id: "SC-158", loop: "invoicing", title: "Find the invoice for Asimex €612.40", status: "in_progress", ownerId: "grazyna", counterparty: "Asimex Sp. z o.o.", amount: -612.4, transactionId: "TX-20261120-0042", category: "food", level: "AUTO" },
    transaction: { id: "TX-20261120-0042", bookingDate: DAY, type: "TRANSFER", reference: "AS-2611 mozzarella", counterparty: "Asimex Sp. z o.o.", amount: -612.4, cardLast4: null },
    steps: [
      ["11:02", "picked_up", "bank", null, "Scout picked up the transfer from Revolut Business"],
      ["11:02", "checked_system", "invoicing", 0, "Searched the invoicing system: 0 matches"],
      ["11:03", "checked_files", "files", null, "Checking Grażyna's company folders…"],
    ],
  },
  {
    task: { id: "SC-157", loop: "invoicing", title: "Ask Vincenzo about Fattoria La Vino €960", status: "waiting", ownerId: "vincenzo", counterparty: "Fattoria La Vino", amount: -960, transactionId: "TX-20261119-0031", category: "beverages", level: "NOTIFY" },
    transaction: { id: "TX-20261119-0031", bookingDate: "2026-11-19", type: "TRANSFER", reference: "FLV-42110", counterparty: "Fattoria La Vino", amount: -960, cardLast4: null },
    steps: [
      ["09:40", "picked_up", "bank", null, "Scout picked up the transfer from Revolut Business"],
      ["09:40", "checked_system", "invoicing", 0, "Searched the invoicing system: 0 matches"],
      ["09:41", "checked_files", "files", 0, "Checked Grażyna's company folders: no receipt"],
      ["09:41", "checked_email", "email", 0, "Checked company email: no receipt"],
      ["09:42", "checked_chat", "chat", 0, "Checked authorized chat history: no context"],
      ["09:42", "asked_person", "slack", null, "Grażyna is on vacation, asked Vincenzo"],
    ],
  },
  {
    task: { id: "SC-156", loop: "invoicing", title: "No invoice from Central Wholesale Market €2,310", status: "escalated", ownerId: "vincenzo", counterparty: "Central Wholesale Market", amount: -2310, category: "food", level: "APPROVAL" },
    steps: [
      ["08:15", "picked_up", "bank", null, "Scout picked up the transfer from Revolut Business"],
      ["08:15", "checked_system", "invoicing", 0, "Searched the invoicing system: 0 matches"],
      ["08:16", "checked_files", "files", 0, "Checked Grażyna's company folders: no receipt"],
      ["08:16", "checked_email", "email", 0, "Checked company email: no receipt"],
      ["08:16", "checked_chat", "chat", 0, "Checked authorized chat history: no context"],
      ["08:17", "asked_person", "slack", null, "Asked Rosa on Slack"],
      ["12:17", "reminder", "slack", null, "No answer from Rosa, sent a reminder"],
      ["16:17", "escalated", "slack", null, "Still no answer, escalated to Vincenzo"],
    ],
  },
  {
    task: { id: "SC-155", loop: "liquidity", title: "Credit line €30,000 for the new oven in March", status: "waiting", ownerId: "vincenzo", requestedBy: "vincenzo", amount: -30000, level: "APPROVAL", simulated: true, category: "equipment" },
    approval: { id: "AP-155", approverId: "vincenzo", question: "Open a €30,000 credit line at 6.9% to buy the oven in March?", status: "pending", answeredBy: null, answeredAt: null },
    steps: [
      ["10:05", "picked_up", "chat", null, "Vincenzo asked: what if I buy an oven for 30k in March?"],
      ["10:05", "note", "forecast", null, "Forecast with the oven: balance dips to €4,200 in April, below the €18,000 safety buffer"],
      ["10:06", "note", "forecast", 3, "Compared 3 options: pay cash, credit line, supplier leasing"],
      ["10:06", "approval_requested", "slack", null, "Proposed a €30,000 credit line, asked Vincenzo to approve"],
    ],
  },
  {
    task: { id: "SC-154", loop: "invoicing", title: "Invoice for Fattoria La Vino €1,840", status: "done", ownerId: "vincenzo", counterparty: "Fattoria La Vino", amount: -1840, transactionId: "TX-20261118-0017", resolvedVia: "slack_dm", outcome: "Filed as FLV-42026", category: "beverages", level: "AUTO",
      evidence: [{ kind: "file", filename: "FLV-42026.pdf", invoice_number: "FLV-42026" }] },
    transaction: { id: "TX-20261118-0017", bookingDate: "2026-11-18", type: "TRANSFER", reference: "FLV-42026 wine Nov", counterparty: "Fattoria La Vino", amount: -1840, cardLast4: null },
    steps: [
      ["14:00", "picked_up", "bank", null, "Scout picked up the transfer from Revolut Business"],
      ["14:00", "checked_system", "invoicing", 0, "Searched the invoicing system: 0 matches"],
      ["14:01", "checked_files", "files", 0, "Checked Grażyna's company folders: no receipt"],
      ["14:01", "checked_email", "email", 0, "Checked company email: no receipt"],
      ["14:01", "checked_chat", "chat", 0, "Checked authorized chat history: no context"],
      ["14:02", "asked_person", "slack", null, "Grażyna is on vacation, asked Vincenzo"],
      ["14:05", "reply_received", "slack", 1, "Vincenzo sent FLV-42026.pdf", { kind: "file", filename: "FLV-42026.pdf" }],
      ["14:05", "resolved", "invoicing", null, "Resolved · €1,840.00 filed as FLV-42026"],
    ],
  },
  {
    task: { id: "SC-153", loop: "invoicing", title: "Asimex AS-2604 invoice found in files", status: "done", ownerId: "grazyna", counterparty: "Asimex Sp. z o.o.", amount: -488.2, resolvedVia: "files", outcome: "Filed as AS-2604", category: "food", level: "AUTO",
      evidence: [{ kind: "file", filename: "AS-2604.pdf", location: "grazyna-mbp: ~/Downloads" }] },
    steps: [
      ["10:12", "picked_up", "bank", null, "Scout picked up the transfer from Revolut Business"],
      ["10:12", "checked_system", "invoicing", 0, "Searched the invoicing system: 0 matches"],
      ["10:13", "checked_files", "files", 1, "Found AS-2604.pdf in Grażyna's Downloads", { kind: "file", filename: "AS-2604.pdf" }],
      ["10:13", "matched", "files", null, "Amount and reference match the payment"],
      ["10:13", "resolved", "invoicing", null, "Resolved · €488.20 filed as AS-2604, told Grażyna on Slack"],
    ],
  },
  {
    task: { id: "SC-152", loop: "invoicing", title: "Central Wholesale Market invoice found in email", status: "done", ownerId: "grazyna", counterparty: "Central Wholesale Market", amount: -1275.9, resolvedVia: "email", outcome: "Filed as CWM-8812", category: "food", level: "AUTO",
      evidence: [{ kind: "email", subject: "Invoice CWM-8812 · November deliveries", from: "billing@cwm.example" }] },
    steps: [
      ["09:03", "picked_up", "bank", null, "Scout picked up the transfer from Revolut Business"],
      ["09:03", "checked_system", "invoicing", 0, "Searched the invoicing system: 0 matches"],
      ["09:04", "checked_files", "files", 0, "Checked Grażyna's company folders: no receipt"],
      ["09:04", "checked_email", "email", 1, "Found the supplier's mail with CWM-8812.pdf", { kind: "email", subject: "Invoice CWM-8812 · November deliveries" }],
      ["09:05", "resolved", "invoicing", null, "Resolved · €1,275.90 filed as CWM-8812"],
    ],
  },
  {
    task: { id: "SC-151", loop: "cost_control", title: "Meta Ads €1,200 over budget: Vanessa's approval found", status: "done", ownerId: "vanessa", counterparty: "Meta Platforms Ireland", amount: -1200, resolvedVia: "chat_context", outcome: "Recorded, nobody asked", category: "marketing", level: "NOTIFY",
      evidence: [{ kind: "chat", quote: "Go ahead with the extra €400 on ads for the truffle week — Vincenzo", author: "#finance", permalink: "https://slack.com/archives/C0FINANCE/p1763550000000100" }] },
    steps: [
      ["17:20", "picked_up", "bank", null, "Scout picked up the card payment from Revolut Business"],
      ["17:20", "note", "invoicing", null, "Ads spend is 50% over the monthly marketing budget"],
      ["17:21", "checked_chat", "chat", 1, "Found the approval in #finance", { kind: "chat", quote: "Go ahead with the extra €400 on ads for the truffle week", permalink: "https://slack.com/archives/C0FINANCE/p1763550000000100" }],
      ["17:21", "resolved", "chat", null, "Resolved · overspend approved in chat, recorded"],
    ],
  },
  {
    task: { id: "SC-150", loop: "liquidity", title: "Watch Caseificio Bianchi refund, due Nov 28", status: "in_progress", ownerId: null, counterparty: "Caseificio Bianchi", amount: 640, resolvedVia: "forecast", category: "food", level: "AUTO" },
    steps: [
      ["15:30", "checked_chat", "chat", 1, "#finance says the burrata delivery was cancelled, refund by Nov 28"],
      ["15:31", "acted", "forecast", null, "Added +€640 to upcoming cash events"],
      ["15:31", "note", "forecast", null, "Watching the bank for the refund"],
    ],
  },
  {
    task: { id: "SC-149", loop: "cost_control", title: "Cloud bill +50% vs online orders +3%", status: "done", ownerId: "vincenzo", counterparty: "Hetzner Online", amount: -186, resolvedVia: "auto", category: "utilities", level: "NOTIFY", outcome: "Alerted Vincenzo" },
    steps: [
      ["07:00", "note", "invoicing", null, "Compared cloud cost with online orders: +50% vs +3%"],
      ["07:01", "acted", "slack", null, "Alerted Vincenzo and the IT owner"],
    ],
  },
  {
    task: { id: "SC-148", loop: "chat", title: "Vanessa asked for files from Vincenzo's laptop: denied", status: "done", ownerId: "vanessa", requestedBy: "vanessa", resolvedVia: "auto", level: "AUTO", outcome: "Access denied" },
    steps: [
      ["13:12", "picked_up", "slack", null, "Vanessa: send me the files from Vincenzo's laptop"],
      ["13:12", "note", "files", null, "Access policy: temp staff cannot read another person's laptop · DENY"],
      ["13:12", "resolved", "slack", null, "Replied to Vanessa: not allowed, logged in the audit log"],
    ],
  },
  {
    task: { id: "SC-147", loop: "liquidity", title: "Review December cash forecast", status: "todo", ownerId: "grazyna", level: "AUTO" },
    steps: [],
  },
  {
    task: { id: "SC-146", loop: "invoicing", title: "Duplicate Asimex transfer marked cancelled", status: "cancelled", ownerId: "grazyna", counterparty: "Asimex Sp. z o.o.", amount: -612.4, resolvedVia: "invoicing" },
    steps: [["08:30", "note", "bank", null, "The bank reversed the duplicate transfer"]],
  },
];

const DAYS: Record<string, string> = { "SC-157": "2026-11-19", "SC-154": "2026-11-18", "SC-153": "2026-11-17", "SC-152": "2026-11-16", "SC-151": "2026-11-15", "SC-149": "2026-11-14", "SC-148": "2026-11-13", "SC-146": "2026-11-12" };

function toTask(fixture: Fixture): AgentTask {
  const day = DAYS[fixture.task.id] ?? DAY;
  const first = fixture.steps[0]?.[0] ?? "08:00";
  const last = fixture.steps.at(-1)?.[0] ?? first;
  const closed = fixture.task.status === "done" || fixture.task.status === "cancelled";
  return {
    ownerId: null, requestedBy: "agent", resolvedVia: null as ResolvedVia | null, outcome: null, level: null, simulated: false, category: null,
    amount: null, currency: "EUR", counterparty: null, transactionId: null, evidence: [],
    pendingApprovalFor: fixture.approval?.status === "pending" ? fixture.approval.approverId : null,
    createdAt: at(first, day), updatedAt: at(last, day), closedAt: closed ? at(last, day) : null,
    ...fixture.task,
  };
}

const AUDIT: Omit<AuditRow, "id">[] = [
  { ts: at("13:12", "2026-11-13"), taskId: "SC-148", actor: "scout", requester: "vanessa", role: "temp staff", action: "files.read", target: "vincenzo-mbp.corp", resource: "~/Documents", decision: "DENY", policy: "files.own-laptop-only", reason: "Temp staff may not read another person's laptop" },
  { ts: at("14:00", "2026-11-18"), taskId: "SC-154", actor: "scout", requester: "agent", role: "invoicing loop", action: "bank.read", target: "Revolut Business", resource: "TX-20261118-0017", decision: "ALLOW", policy: "invoicing.bank-read", reason: null },
  { ts: at("14:00", "2026-11-18"), taskId: "SC-154", actor: "scout", requester: "agent", role: "invoicing loop", action: "invoicing.search", target: "Invoicing (Postgres)", resource: "invoices", decision: "ALLOW", policy: "invoicing.system-read", reason: null },
  { ts: at("14:01", "2026-11-18"), taskId: "SC-154", actor: "scout", requester: "agent", role: "invoicing loop", action: "files.search", target: "grazyna-mbp.corp", resource: "~/Downloads, ~/Invoices", decision: "ALLOW", policy: "invoicing.office-folders", reason: null },
  { ts: at("14:01", "2026-11-18"), taskId: "SC-154", actor: "scout", requester: "agent", role: "invoicing loop", action: "mail.search", target: "Email (Gmail)", resource: "label:Amalfi", decision: "ALLOW", policy: "invoicing.mail-label", reason: null },
  { ts: at("14:01", "2026-11-18"), taskId: "SC-154", actor: "scout", requester: "agent", role: "invoicing loop", action: "chat.search", target: "Slack", resource: "#finance", decision: "ALLOW", policy: "chat.allowlisted-channels", reason: null },
  { ts: at("14:02", "2026-11-18"), taskId: "SC-154", actor: "scout", requester: "agent", role: "invoicing loop", action: "slack.dm", target: "Vincenzo Amalfi", resource: "DM", decision: "ALLOW", policy: "invoicing.ask-owner", reason: "Grażyna is on vacation" },
  { ts: at("14:05", "2026-11-18"), taskId: "SC-154", actor: "scout", requester: "agent", role: "invoicing loop", action: "invoicing.file", target: "Invoicing (Postgres)", resource: "FLV-42026", decision: "ALLOW", policy: "invoicing.file-found-document", reason: "AUTO" },
  { ts: at("10:13", "2026-11-17"), taskId: "SC-153", actor: "scout", requester: "agent", role: "invoicing loop", action: "files.read", target: "grazyna-mbp.corp", resource: "~/Downloads/AS-2604.pdf", decision: "ALLOW", policy: "invoicing.office-folders", reason: null },
  { ts: at("10:06"), taskId: "SC-155", actor: "scout", requester: "vincenzo", role: "CEO", action: "approval.request", target: "Vincenzo Amalfi", resource: "credit line €30,000", decision: "ALLOW", policy: "liquidity.ceo-approval", reason: "APPROVAL level" },
  { ts: at("11:03"), taskId: "SC-158", actor: "scout", requester: "agent", role: "invoicing loop", action: "files.search", target: "grazyna-mbp.corp", resource: "~/Downloads", decision: "ALLOW", policy: "invoicing.office-folders", reason: null },
  { ts: at("09:20", "2026-11-19"), taskId: null, actor: "scout", requester: "rosa", role: "head chef", action: "mail.read", target: "Email (Gmail)", resource: "label:Payroll", decision: "DENY", policy: "mail.label-scope", reason: "Payroll mail is limited to the CEO and office manager" },
];

function steps(fixture: Fixture): TaskStep[] {
  const day = DAYS[fixture.task.id] ?? DAY;
  return fixture.steps.map(([time, kind, source, resultCount, text, evidence], index) =>
    ({ id: index + 1, ts: at(time, day), kind, source, resultCount, text, evidence: evidence ?? null }));
}

export function getPreviewTask(id: string): TaskDetail | null {
  const fixture = fixtures.find(entry => entry.task.id === id);
  if (!fixture) return null;
  return {
    task: toTask(fixture), steps: steps(fixture), transaction: fixture.transaction ?? null,
    approval: fixture.approval ? { ...fixture.approval, createdAt: at("10:06") } : null,
    audit: AUDIT.map((row, index) => ({ ...row, id: index + 1 })).filter(row => row.taskId === id),
  };
}

export function getPreviewData(): DashboardData {
  const period = (scale: number) => financePeriod([
    { category: "revenue", count: "120", income: String(12000000 * scale), expense: "0" },
    { category: "payroll", count: "12", income: "0", expense: String(3600000 * scale) },
    { category: "food", count: "24", income: "0", expense: String(2400000 * scale) },
    { category: "rent", count: "12", income: "0", expense: String(1200000 * scale) },
    { category: "utilities", count: "12", income: "0", expense: String(600000 * scale) },
    { category: "tax", count: "4", income: "0", expense: String(400000 * scale) },
  ]);
  const tasks = fixtures.map(toTask);
  return {
    asOf: DAY, yearLabel: "2026", year: period(1), q4: period(0.25),
    closingBalance: 38000, monthlyPayroll: 9000, monthlyContributions: 1800,
    undocumentedPayments: 2,
    employees: [
      { id: "vincenzo", name: "Vincenzo Amalfi", initials: "VA", role: "CEO / owner", team: "Front of house", salary: 4200, avatar: "avatar-green" },
      { id: "grazyna", name: "Grażyna Nowak", initials: "GN", role: "Office manager", team: "Front of house", salary: 2900, avatar: "avatar-orange" },
      { id: "rosa", name: "Rosa Pavese", initials: "RP", role: "Head chef", team: "Kitchen", salary: 3400, avatar: "avatar-green" },
      { id: "vanessa", name: "Vanessa Paradi", initials: "VP", role: "Waitstaff / marketing (temp)", team: "Front of house", salary: null, avatar: "avatar-green" },
    ],
    scout: {
      ceoId: "vincenzo", tasks, demo: process.env.SCOUT_DEMO === "1",
      explained: { documented: 310, total: 312 },
      connections: [
        { id: "bank", kind: "bank", provider: "revolut-mock", enabled: true, displayName: "Revolut Business", config: { mocked: true }, status: "connected", lastSyncAt: at("11:02"), lastError: null },
        { id: "invoicing", kind: "invoicing", provider: "postgres", enabled: true, displayName: "Invoicing", config: { invoices: 4182 }, status: "connected", lastSyncAt: at("11:02"), lastError: null },
        { id: "ksef", kind: "invoicing", provider: "ksef_test", enabled: true, displayName: "KSeF · test environment", status: "connected", lastSyncAt: at("11:03"), lastError: null, config: {
          env: "test", api: "https://api-test.ksef.mf.gov.pl/v2", nip: "5621254863", company: "Pizzeria Amalfi Sp. z o.o.", phase: "done",
          steps: [
            { at: "2026-11-21T11:02:41Z", text: "Signed in to KSeF as NIP 5621254863 (XAdES, test seal)", ok: true },
            { at: "2026-11-21T11:02:43Z", text: "Issued 4 test invoices from suppliers", ok: true },
            { at: "2026-11-21T11:02:47Z", text: "Received 4 invoices from KSeF", ok: true },
            { at: "2026-11-21T11:02:48Z", text: "Matched 3 invoices to bank payments", ok: true },
          ],
          issued: 4, received: 4, imported: 4, linked: 3, linked_total: "1727.30",
          invoices: [
            { number: "AS-12345", ksef_number: "5621254863-20261121-01B739400000-6F", supplier: "Asimex Wholesale Ltd", total: "623.48", currency: "EUR", linked: true },
            { number: "FLV-42110", ksef_number: "5621254863-20261121-02C84A100000-3A", supplier: "Fattoria La Vino", total: "960.00", currency: "EUR", linked: true },
            { number: "CB-7781", ksef_number: "5621254863-20261121-03D95B200000-8E", supplier: "Caseificio Bianchi", total: "143.82", currency: "EUR", linked: true },
            { number: "CWM-9034", ksef_number: "5621254863-20261121-04EA6C300000-1B", supplier: "Central Wholesale Market", total: "512.10", currency: "EUR", linked: false },
          ],
        } },
        { id: "mail", kind: "mail", provider: "gmail", enabled: true, displayName: "Email", config: { account: "office@pizzeria-amalfi.example", label: "Amalfi" }, status: "disconnected", lastSyncAt: null, lastError: null },
        { id: "slack", kind: "chat", provider: "slack", enabled: true, displayName: "Slack", config: { channels: ["finance"], scopes: ["chat:write", "im:history", "channels:history", "files:read", "reactions:write"], missing_scopes: ["channels:join"] }, status: "connected", lastSyncAt: at("11:01"), lastError: null },
        { id: "laptops", kind: "files", provider: "workstations", enabled: true, displayName: "Company laptops", config: { hosts: ["grazyna-mbp.corp", "vincenzo-mbp.corp", "rosa-mbp.corp"] }, status: "connected", lastSyncAt: at("10:58"), lastError: null },
      ],
      cashEvents: [
        { id: 1, onDate: "2026-11-28", amount: 640, label: "Caseificio Bianchi refund · from chat context", taskId: "SC-150" },
        { id: 2, onDate: "2026-11-30", amount: -9000, label: "Payroll run", taskId: null },
        { id: 3, onDate: "2026-12-01", amount: -3200, label: "Rent · Via Roma 12", taskId: null },
        { id: 4, onDate: "2026-12-05", amount: -1840, label: "Fattoria La Vino · standing order", taskId: null },
      ],
    },
    forecast: sampleForecast(90, 1),
    forecastYear: sampleForecast(365, 7),
    generatedAt: `${DAY}T12:00:00Z`,
    quarterSpend: quarterSpend("2026-10-01", DAY, [
      { id: "kitchen", name: "Kitchen / food", budget: "8520000", spent: "5798507" },
      { id: "front", name: "Front of house and admin", budget: "2835000", spent: "1275000" },
      { id: "marketing", name: "Marketing", budget: "255000", spent: "312000" },
      { id: "it", name: "IT / online-order app", budget: "75000", spent: "0" },
      { id: null, name: "Unassigned", budget: null, spent: "1588834" },
    ]),
  };
}

export function getPreviewRecords(): ScoutRecords {
  return {
    audit: AUDIT.map((row, index) => ({ ...row, id: index + 1 })).sort((a, b) => b.ts.localeCompare(a.ts)),
    transactions: [
        { id: "TX-20261120-0042", bookingDate: DAY, counterparty: "Asimex Sp. z o.o.", reference: "AS-2611 mozzarella", amount: -612.4, category: "food", invoiceNumber: "AS-2611", documented: false },
        { id: "TX-20261120-0040", bookingDate: DAY, counterparty: "Card payments · SumUp", reference: "Daily settlement", amount: 2431.5, category: "revenue", invoiceNumber: null, documented: true },
        { id: "TX-20261119-0031", bookingDate: "2026-11-19", counterparty: "Fattoria La Vino", reference: "FLV-42110", amount: -960, category: "beverages", invoiceNumber: "FLV-42110", documented: false },
        { id: "TX-20261118-0017", bookingDate: "2026-11-18", counterparty: "Fattoria La Vino", reference: "FLV-42026 wine Nov", amount: -1840, category: "beverages", invoiceNumber: "FLV-42026", documented: true },
        { id: "TX-20261117-0009", bookingDate: "2026-11-17", counterparty: "Asimex Sp. z o.o.", reference: "AS-2604", amount: -488.2, category: "food", invoiceNumber: "AS-2604", documented: true },
        { id: "TX-20261116-0004", bookingDate: "2026-11-16", counterparty: "Central Wholesale Market", reference: "CWM-8812", amount: -1275.9, category: "food", invoiceNumber: "CWM-8812", documented: true },
      ],
  };
}

/** A sample forecast walked from 38k: daily or weekly points, the lowest one marked like the API does. */
function sampleForecast(days: number, every: number): Forecast {
  const start = Date.parse(`${DAY}T00:00:00Z`);
  const points = Array.from({ length: Math.floor(days / every) + 1 }, (_, index) => {
    const day = index * every;
    return { day, date: new Date(start + day * 86_400_000).toISOString().slice(0, 10), balance: Math.round(38000 - day * 60 + Math.sin(day / 8) * 2300) };
  });
  const lowest = points.reduce((low, point) => point.balance < low.balance ? point : low);
  return { asOf: DAY, model: "Sample forecast", horizonDays: days, openingBalance: 38000, safetyBuffer: 18000, lowest: { date: lowest.date, balance: lowest.balance }, points };
}
