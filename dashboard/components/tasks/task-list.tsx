"use client";
import { StrategySettingsPanel } from "@/components/strategy-settings";

import Link from "next/link";
import { useMemo } from "react";
import { ChevronRight, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { PriorityBars, StatusIcon } from "@/components/tasks/status-icon";
import { channelChip, groupByStatus, initials, LOOP_LABEL, priority, STATUS_LABEL, STATUS_ORDER, type AgentTask, type TaskStatus } from "@/lib/scout-model";
import type { TaskFilter, TaskScope } from "@/lib/nav";
import { filterTasks, inInbox, unseenResult, type SeenMap } from "@/lib/task-signals";
import { cn } from "@/lib/utils";


export function OwnerAvatar({ name, className }: { name: string; className?: string }) {
  return <Avatar className={cn("size-5", className)} title={name}>
    <AvatarFallback className="bg-secondary text-[9px] font-medium">{initials(name)}</AvatarFallback>
  </Avatar>;
}

export function ChannelChip({ task }: { task: AgentTask }) {
  const chip = channelChip(task);
  if (!chip) return null;
  return <Badge variant="outline" className={cn("h-5 shrink-0 rounded-full px-2 text-[11px] font-normal text-muted-foreground",
    chip === "Approval" && "border-danger-border text-danger")}>{chip}</Badge>;
}

/** Opens the task in place (plain click) or in a new tab (modifier click). */
export type OpenTask = (task: AgentTask) => void;

export function TaskRow({ task, ownerName, unseen = false, onOpen }: { task: AgentTask; ownerName: string | null; unseen?: boolean; onOpen?: OpenTask }) {
  return <li>
    <Link href={`#task/${encodeURIComponent(task.id)}`} data-task-id={task.id}
      onClick={event => { if (!onOpen || event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); onOpen(task); }}
      className="flex h-10 items-center gap-3 border-b px-4 text-[13px] hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none">
      <PriorityBars value={priority(task)} />
      <span className="w-14 shrink-0 font-mono text-[12px] text-muted-foreground">{task.id}</span>
      <StatusIcon status={task.status} />
      <span className={cn("min-w-0 flex-1 truncate", task.status === "cancelled" && "text-muted-foreground line-through", unseen && "font-medium")}>{task.title}</span>
      {unseen && <span className="size-1.5 shrink-0 rounded-full bg-primary" title="New result" aria-label="New result" />}
      <span className="hidden shrink-0 text-[11px] text-muted-foreground md:inline">{LOOP_LABEL[task.loop] ?? task.loop}</span>
      {task.simulated && <Badge variant="outline" className="h-5 px-1.5 text-[10px] font-normal">SIMULATED</Badge>}
      <ChannelChip task={task} />
      <span className="flex w-5 shrink-0 justify-end">{ownerName && <OwnerAvatar name={ownerName} />}</span>
    </Link>
  </li>;
}

const selectClass = "h-8 rounded-md border bg-card px-2 text-[13px] text-foreground";
const SCOPES: { value: TaskScope | undefined; label: string }[] = [
  { value: undefined, label: "All" }, { value: "inbox", label: "Needs me" }, { value: "mine", label: "Mine" },
];

/** The one task list: every agent task, filtered by scope (all / needs me / mine), loop, status and owner.
 *  The filter lives in the URL hash, so sidebar items (Inbox, loops) are just presets of it. */
export function TaskBoard({ tasks, names, ceo, filter, onFilter, seen, onOpen }: {
  tasks: AgentTask[]; names: Record<string, string>; ceo: string; filter: TaskFilter; onFilter: (filter: TaskFilter) => void;
  seen: SeenMap; onOpen: OpenTask;
}) {
  const owners = useMemo(() => [...new Set(tasks.map(task => task.ownerId).filter((id): id is string => !!id))], [tasks]);
  const loops = useMemo(() => [...new Set([...Object.keys(LOOP_LABEL).filter(loop => loop !== "expenses"), ...tasks.map(task => task.loop)])], [tasks]);
  const visible = filterTasks(tasks, filter, ceo);
  const groups = groupByStatus(visible);
  const nameOf = (id: string | null) => id ? names[id] ?? id : null;
  const scopeCount = (scope: TaskScope | undefined) => filterTasks(tasks, { ...filter, scope, status: undefined }, ceo).filter(task => scope === "inbox" ? inInbox(task, ceo) : !["done", "cancelled"].includes(task.status)).length;
  const filtered = !!(filter.scope || filter.loop || filter.status || filter.owner);
  const title = filter.scope === "inbox" ? "Inbox" : filter.loop ? `${LOOP_LABEL[filter.loop] ?? filter.loop} loop` : filter.scope === "mine" ? "My tasks" : "Tasks";

  return <section id="scout-task-board" className="overflow-hidden rounded-xl border bg-card">
    <header className="space-y-3 border-b px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="mr-auto min-w-0">
          <h1 className="text-[15px] font-medium">{title} <span className="ml-1 text-[13px] font-normal text-muted-foreground">{visible.length}</span></h1>
          <p className="mt-0.5 text-xs text-muted-foreground">{filter.loop === "strategy" ? "Company news from your external provider, every 24 hours. Each run is recorded below." : filter.scope === "inbox" ? "Waiting on or escalated to you, including pending approvals." : "Everything Scout works on, from every loop. Filter by loop, status or owner."}</p>
        </div>
        <div className="flex rounded-lg border bg-muted/40 p-0.5" role="group" aria-label="Scope">
          {SCOPES.map(scope => { const active = filter.scope === scope.value; const count = scopeCount(scope.value);
            return <button key={scope.label} type="button" id={`scout-task-scope-${scope.value ?? "all"}`} aria-pressed={active}
              onClick={() => onFilter({ ...filter, scope: scope.value })}
              className={cn("inline-flex h-7 items-center gap-1.5 rounded-md px-2.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground", active && "bg-background font-medium text-foreground shadow-xs")}>
              {scope.label}{scope.value && count > 0 && <span className={cn("font-mono text-[10px]", scope.value === "inbox" && count > 0 && "text-warning")}>{count}</span>}
            </button>; })}
        </div>
      </div>
      {filter.loop === "strategy" && <StrategySettingsPanel />}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter tasks">
        <select id="scout-task-filter-loop" aria-label="Loop" className={selectClass} value={filter.loop ?? ""} onChange={event => onFilter({ ...filter, loop: event.target.value || undefined })}>
          <option value="">All loops</option>
          {loops.map(loop => <option key={loop} value={loop}>{LOOP_LABEL[loop] ?? loop}</option>)}
        </select>
        <select id="scout-task-filter-status" aria-label="Status" className={selectClass} value={filter.status ?? ""} onChange={event => onFilter({ ...filter, status: (event.target.value || undefined) as TaskStatus | undefined })}>
          <option value="">All statuses</option>
          {STATUS_ORDER.map(status => <option key={status} value={status}>{STATUS_LABEL[status]}</option>)}
        </select>
        <select id="scout-task-filter-owner" aria-label="Owner" className={selectClass} value={filter.owner ?? ""} onChange={event => onFilter({ ...filter, owner: event.target.value || undefined })}>
          <option value="">All owners</option>
          {owners.map(owner => <option key={owner} value={owner}>{nameOf(owner)}</option>)}
        </select>
        {filtered && <Button id="scout-task-filter-clear" variant="ghost" size="sm" className="h-8 gap-1 text-muted-foreground" onClick={() => onFilter({})}><X className="size-3.5" aria-hidden="true" />Clear</Button>}
      </div>
    </header>
    {groups.length === 0 ? <p role="status" className="px-4 py-10 text-center text-sm text-muted-foreground">{filtered ? "No tasks match these filters." : "No tasks here. Scout will add them as its loops find work."}</p> :
      groups.map(group => {
        const rows = <ul>{group.tasks.map(task => <TaskRow key={task.id} task={task} ownerName={nameOf(task.ownerId)} unseen={unseenResult(task, seen)} onOpen={onOpen} />)}</ul>;
        const heading = <><StatusIcon status={group.status} />{group.label}<span className="font-normal text-muted-foreground">{group.tasks.length}</span></>;
        return group.status === "cancelled"
          ? <details key={group.status} className="group">
              <summary className="flex h-9 cursor-pointer list-none items-center gap-2 border-b bg-muted/40 px-4 text-[13px] font-medium">
                <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />{heading}
              </summary>{rows}
            </details>
          : <div key={group.status}><h2 className="flex h-9 items-center gap-2 border-b bg-muted/40 px-4 text-[13px] font-medium">{heading}</h2>{rows}</div>;
      })}
  </section>;
}

/** Compact list for the overview: the few tasks that need a person, linking to the full task list. */
export function AttentionList({ tasks, names, onOpen, onShowAll, limit = 8 }: { tasks: AgentTask[]; names: Record<string, string>; onOpen: OpenTask; onShowAll: () => void; limit?: number }) {
  return <section id="scout-attention-list" aria-labelledby="scout-attention-title" className="h-fit overflow-hidden rounded-xl border bg-card">
    <header className="flex items-center justify-between gap-3 border-b px-4 py-2.5">
      <h2 id="scout-attention-title" className="text-[13px] font-medium">Open tasks <span className="ml-1 font-normal text-muted-foreground">{tasks.length}</span></h2>
      <Button id="scout-attention-all" variant="ghost" size="sm" className="h-7 text-xs text-muted-foreground" onClick={onShowAll}>All tasks<ChevronRight className="size-3.5" aria-hidden="true" /></Button>
    </header>
    {tasks.length === 0 ? <p className="px-4 py-6 text-sm text-muted-foreground">No open tasks.</p>
      : <ul>{tasks.slice(0, limit).map(task => <TaskRow key={task.id} task={task} ownerName={task.ownerId ? names[task.ownerId] ?? task.ownerId : null} onOpen={onOpen} />)}</ul>}
  </section>;
}
