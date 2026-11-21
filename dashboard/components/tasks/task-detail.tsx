"use client";

import Link from "next/link";
import { tenantFetch } from "@/lib/tenant-browser";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Bot, CircleCheck, Database, FileText, Folder, Landmark, LineChart, Loader2, Mail, MessageSquare, MessagesSquare, Send, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AuditTable } from "@/components/audit-log";
import { OwnerAvatar } from "@/components/tasks/task-list";
import { StatusIcon } from "@/components/tasks/status-icon";
import { clock, evidenceView, fullTimestamp, LOOP_LABEL, money, RESOLVED_VIA_LABEL, shortDate, STATUS_LABEL, type Evidence, type TaskDetail } from "@/lib/scout-model";
import { cn } from "@/lib/utils";

const SOURCE_ICON: Record<string, LucideIcon> = {
  bank: Landmark, invoicing: Database, files: Folder, email: Mail, mail: Mail, chat: MessagesSquare, slack: Send, forecast: LineChart,
};

function EvidenceItem({ evidence }: { evidence: Evidence }) {
  const view = evidenceView(evidence);
  const Icon = view.kind === "file" ? FileText : view.kind === "email" ? Mail : view.kind === "chat" ? MessageSquare : Bot;
  const body = <span className="min-w-0"><span className={cn("block break-words", view.kind === "chat" && "italic")}>{view.kind === "chat" ? `“${view.label}”` : view.label}</span>
    {view.detail && <span className="block text-[11px] text-muted-foreground">{view.detail}</span>}</span>;
  return <li className="flex items-start gap-2 text-[13px]"><Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
    {view.href ? <a href={view.href} target="_blank" rel="noreferrer noopener" className="underline-offset-2 hover:underline">{body}</a> : body}</li>;
}

function Property({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="grid grid-cols-[96px_minmax(0,1fr)] items-start gap-2 py-1.5 text-[13px]"><dt className="text-muted-foreground">{label}</dt><dd className="min-w-0">{children}</dd></div>;
}

export function TaskDetailView({ detail, names, onChanged }: { detail: TaskDetail; names: Record<string, string>; onChanged?: () => void }) {
  const { task, steps, approval, audit, transaction } = detail;
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);
  const [answering, setAnswering] = useState<"approve" | "reject" | null>(null);
  const owner = task.ownerId ? names[task.ownerId] ?? task.ownerId : null;
  const running = task.status === "in_progress";
  const evidence = [...task.evidence, ...steps.flatMap(step => step.evidence ? [step.evidence] : [])]
    .filter((item, index, all) => all.findIndex(other => evidenceView(other).label === evidenceView(item).label) === index);
  const subtitle = [
    transaction?.cardLast4 ? `Company card •${transaction.cardLast4}` : transaction?.id ?? task.transactionId,
    transaction ? shortDate(`${transaction.bookingDate}T12:00:00Z`) : shortDate(task.createdAt),
    task.amount !== null ? money(task.amount, task.currency) : null,
  ].filter(Boolean).join(" · ");

  async function answer(action: "approve" | "reject") {
    if (!approval) return;
    setMessage(null); setAnswering(action);
    const response = await tenantFetch("/api/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: approval.id, action }) })
      .catch(() => null);
    setAnswering(null);
    if (!response?.ok) { setMessage("Scout could not record the answer. Try again in a moment."); return; }
    setMessage(action === "approve" ? "Approved. Scout continues the task." : "Rejected. Scout closes the proposal.");
    startTransition(() => { router.refresh(); onChanged?.(); });
  }

  return <div className="space-y-5">
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
      <Link href="#tasks" className="hover:text-foreground">Tasks</Link><span aria-hidden="true">›</span><span className="font-mono text-foreground">{task.id}</span>
    </nav>
    <header className="space-y-1">
      <h1 className="text-2xl font-medium tracking-tight">{task.counterparty ?? task.title}</h1>
      {task.counterparty && <p className="text-sm">{task.title}</p>}
      {subtitle && <p className="font-mono text-xs text-muted-foreground">{subtitle}</p>}
    </header>
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-5">
        <section aria-labelledby="activity-title" className="rounded-xl border bg-card">
          <h2 id="activity-title" className="border-b px-4 py-2.5 text-[13px] font-medium">Activity log</h2>
          {steps.length === 0 ? <p className="px-4 py-6 text-sm text-muted-foreground">No activity yet.</p> :
            <ol className="relative px-4 py-2">
              {steps.map((step, index) => {
                const latest = index === steps.length - 1;
                const Icon = step.kind === "resolved" ? CircleCheck : SOURCE_ICON[step.source ?? ""] ?? Bot;
                return <li key={step.id} className="relative flex items-start gap-3 py-2 text-[13px]">
                  {index < steps.length - 1 && <span aria-hidden="true" className="absolute top-7 bottom-[-6px] left-[11px] w-px bg-border" />}
                  <span className={cn("relative flex size-6 shrink-0 items-center justify-center rounded-full border bg-background",
                    step.kind === "resolved" && "border-success-border text-success", step.kind === "escalated" && "border-danger-border text-danger")}>
                    {latest && running ? <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Running" /> : <Icon className="size-3.5" aria-hidden="true" />}
                  </span>
                  <span className="min-w-0 flex-1 pt-0.5">{step.text}
                    {step.resultCount !== null && <Badge variant="secondary" className="ml-2 h-5 px-1.5 align-middle font-mono text-[10px] font-normal">{step.resultCount} {step.resultCount === 1 ? "result" : "results"}</Badge>}
                  </span>
                  <time dateTime={step.ts} title={fullTimestamp(step.ts)} className="shrink-0 pt-0.5 font-mono text-xs text-muted-foreground">{clock(step.ts)}</time>
                </li>;
              })}
            </ol>}
        </section>

        {approval && <section aria-labelledby="approval-title" className={cn("space-y-3 rounded-xl border p-4", approval.status === "pending" ? "border-warning-border bg-warning-muted" : "bg-card")}>
          <div className="flex items-center justify-between gap-3"><h2 id="approval-title" className="text-[13px] font-medium">Approval</h2>
            <Badge variant="outline" className="font-normal">{approval.status}</Badge></div>
          <p className="text-sm">{approval.question}</p>
          {approval.status === "pending" ? <div className="flex gap-2">
            <Button id="scout-approval-approve" size="sm" disabled={pending || answering !== null} aria-busy={answering === "approve"} onClick={() => answer("approve")}>{answering === "approve" && <Loader2 className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />}Approve</Button>
            <Button id="scout-approval-reject" size="sm" variant="outline" disabled={pending || answering !== null} aria-busy={answering === "reject"} onClick={() => answer("reject")}>{answering === "reject" && <Loader2 className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />}Reject</Button>
          </div> : <p className="text-xs text-muted-foreground">Answered by {approval.answeredBy ? names[approval.answeredBy] ?? approval.answeredBy : "—"}{approval.answeredAt ? ` · ${fullTimestamp(approval.answeredAt)}` : ""}</p>}
          {message && <p role="status" className="text-xs">{message}</p>}
        </section>}

        <section aria-labelledby="task-audit-title" className="rounded-xl border bg-card">
          <h2 id="task-audit-title" className="border-b px-4 py-2.5 text-[13px] font-medium">Audit log for {task.id}</h2>
          <AuditTable rows={audit} compact />
        </section>
      </div>

      <aside aria-label="Task properties" className="h-fit rounded-xl border bg-card px-4 py-3">
        <dl>
          <Property label="Status"><span className="inline-flex items-center gap-2"><StatusIcon status={task.status} />{task.status === "done" ? "Resolved" : STATUS_LABEL[task.status]}</span></Property>
          <Property label="Loop">{LOOP_LABEL[task.loop] ?? task.loop}</Property>
          <Property label="Owner">{owner ? <span className="inline-flex items-center gap-2"><OwnerAvatar name={owner} />{owner}</span> : <span className="text-muted-foreground">Scout</span>}</Property>
          <Property label="Level">{task.level ? <Badge variant="outline" className="font-normal">{task.level}</Badge> : "—"}{task.simulated && <Badge variant="outline" className="ml-1 font-normal">SIMULATED</Badge>}</Property>
          <Property label="Resolved via">{task.resolvedVia ? RESOLVED_VIA_LABEL[task.resolvedVia] : "—"}</Property>
          <Property label="Evidence">{evidence.length ? <ul className="space-y-1.5">{evidence.map((item, index) => <EvidenceItem key={index} evidence={item} />)}</ul> : "—"}</Property>
          <Property label="Category">{task.category ? task.category.replaceAll("_", " ") : "—"}</Property>
          <Property label="Transaction">{transaction ? <span className="block"><span className="font-mono text-xs">{transaction.id}</span>
            <span className="block text-xs text-muted-foreground">{transaction.reference} · {money(transaction.amount)}</span></span> : task.transactionId ? <span className="font-mono text-xs">{task.transactionId}</span> : "—"}</Property>
          {task.outcome && <Property label="Outcome">{task.outcome}</Property>}
        </dl>
      </aside>
    </div>
  </div>;
}

/** Same layout as the task page while it loads, so opening a task never shows a blank screen. */
export function TaskDetailSkeleton({ id }: { id: string }) {
  return <div className="space-y-5" role="status" aria-label={`Loading ${id}`}>
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[13px] text-muted-foreground">
      <Link href="#tasks" className="hover:text-foreground">Tasks</Link><span aria-hidden="true">›</span><span className="font-mono text-foreground">{id}</span>
    </nav>
    <div className="space-y-2"><Skeleton className="h-7 w-72" /><Skeleton className="h-4 w-96 max-w-full" /><Skeleton className="h-3 w-48" /></div>
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-5">
        <div className="rounded-xl border bg-card"><div className="border-b px-4 py-2.5 text-[13px] font-medium">Activity log</div>
          <div className="space-y-4 px-4 py-4">{[0, 1, 2, 3].map(row => <div key={row} className="flex items-center gap-3"><Skeleton className="size-6 rounded-full" /><Skeleton className="h-4 flex-1" /><Skeleton className="h-3 w-10" /></div>)}</div>
        </div>
      </div>
      <div className="h-fit space-y-3 rounded-xl border bg-card px-4 py-4">{[0, 1, 2, 3, 4, 5].map(row => <div key={row} className="grid grid-cols-[96px_1fr] gap-2"><Skeleton className="h-4 w-16" /><Skeleton className="h-4" /></div>)}</div>
    </div>
  </div>;
}
