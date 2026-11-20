"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { PriorityBars, StatusIcon } from "@/components/tasks/status-icon";
import { channelChip, groupByStatus, initials, LOOP_LABEL, priority, STATUS_LABEL, STATUS_ORDER, type AgentTask, type TaskStatus } from "@/lib/scout-model";
import { cn } from "@/lib/utils";

export type TaskFilter = { loop?: string; owner?: string; status?: TaskStatus };

export function OwnerAvatar({ name, className }: { name: string; className?: string }) {
  return <Avatar className={cn("size-5", className)} title={name}>
    <AvatarFallback className="bg-secondary text-[9px] font-medium">{initials(name)}</AvatarFallback>
  </Avatar>;
}

export function ChannelChip({ task }: { task: AgentTask }) {
  const chip = channelChip(task);
  if (!chip) return null;
  return <Badge variant="outline" className={cn("h-5 shrink-0 rounded-full px-2 text-[11px] font-normal text-muted-foreground",
    chip === "CFO" && "border-danger-border text-danger")}>{chip}</Badge>;
}

export function TaskRow({ task, ownerName }: { task: AgentTask; ownerName: string | null }) {
  return <li>
    <Link href={`/tasks/${encodeURIComponent(task.id)}`} className="flex h-10 items-center gap-3 border-b px-4 text-[13px] hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none">
      <PriorityBars value={priority(task)} />
      <span className="w-14 shrink-0 font-mono text-[12px] text-muted-foreground">{task.id}</span>
      <StatusIcon status={task.status} />
      <span className={cn("min-w-0 flex-1 truncate", task.status === "cancelled" && "text-muted-foreground line-through")}>{task.title}</span>
      {task.simulated && <Badge variant="outline" className="h-5 px-1.5 text-[10px] font-normal">SIMULATED</Badge>}
      <ChannelChip task={task} />
      <span className="flex w-5 shrink-0 justify-end">{ownerName && <OwnerAvatar name={ownerName} />}</span>
    </Link>
  </li>;
}

const selectClass = "h-8 rounded-md border bg-card px-2 text-[13px] text-foreground";

/** Agent tasks grouped by status (In progress, Waiting, Escalated, Todo, Done; Cancelled collapsed). */
export function TaskList({ tasks, names, title, description, initial = {}, filters = true }: {
  tasks: AgentTask[]; names: Record<string, string>; title: string; description?: string; initial?: TaskFilter; filters?: boolean;
}) {
  const [filter, setFilter] = useState<TaskFilter>(initial);
  const owners = useMemo(() => [...new Set(tasks.map(task => task.ownerId).filter((id): id is string => !!id))], [tasks]);
  const loops = useMemo(() => [...new Set(tasks.map(task => task.loop))], [tasks]);
  const visible = tasks.filter(task => (!filter.loop || task.loop === filter.loop) && (!filter.owner || task.ownerId === filter.owner)
    && (!filter.status || task.status === filter.status));
  const groups = groupByStatus(visible);
  const nameOf = (id: string | null) => id ? names[id] ?? id : null;

  return <section className="overflow-hidden rounded-xl border bg-card">
    <header className="flex flex-wrap items-center gap-3 border-b px-4 py-3">
      <div className="mr-auto min-w-0">
        <h1 className="text-[15px] font-medium">{title} <span className="ml-1 text-[13px] font-normal text-muted-foreground">{visible.length}</span></h1>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      {filters && <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter tasks">
        <select aria-label="Loop" className={selectClass} value={filter.loop ?? ""} onChange={event => setFilter({ ...filter, loop: event.target.value || undefined })}>
          <option value="">All loops</option>
          {loops.map(loop => <option key={loop} value={loop}>{LOOP_LABEL[loop] ?? loop}</option>)}
        </select>
        <select aria-label="Owner" className={selectClass} value={filter.owner ?? ""} onChange={event => setFilter({ ...filter, owner: event.target.value || undefined })}>
          <option value="">All owners</option>
          {owners.map(owner => <option key={owner} value={owner}>{nameOf(owner)}</option>)}
        </select>
        <select aria-label="Status" className={selectClass} value={filter.status ?? ""} onChange={event => setFilter({ ...filter, status: (event.target.value || undefined) as TaskStatus | undefined })}>
          <option value="">All statuses</option>
          {STATUS_ORDER.map(status => <option key={status} value={status}>{STATUS_LABEL[status]}</option>)}
        </select>
      </div>}
    </header>
    {groups.length === 0 ? <p role="status" className="px-4 py-10 text-center text-sm text-muted-foreground">No tasks here. Scout will add them as its loops find work.</p> :
      groups.map(group => group.status === "cancelled"
        ? <details key={group.status} className="group">
            <summary className="flex h-9 cursor-pointer list-none items-center gap-2 border-b bg-muted/40 px-4 text-[13px] font-medium">
              <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
              <StatusIcon status={group.status} />{group.label}<span className="font-normal text-muted-foreground">{group.tasks.length}</span>
            </summary>
            <ul>{group.tasks.map(task => <TaskRow key={task.id} task={task} ownerName={nameOf(task.ownerId)} />)}</ul>
          </details>
        : <div key={group.status}>
            <h2 className="flex h-9 items-center gap-2 border-b bg-muted/40 px-4 text-[13px] font-medium">
              <StatusIcon status={group.status} />{group.label}<span className="font-normal text-muted-foreground">{group.tasks.length}</span>
            </h2>
            <ul>{group.tasks.map(task => <TaskRow key={task.id} task={task} ownerName={nameOf(task.ownerId)} />)}</ul>
          </div>)}
  </section>;
}
