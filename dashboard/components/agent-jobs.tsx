"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, CircleCheck, CircleDashed, CirclePause, CircleX, Clock3, LoaderCircle, RefreshCw, Search } from "lucide-react";
import type { AgentJob, JobFilter, JobStatus } from "@/lib/agent-jobs";
import { filterJobs, formatModelTime, jobReference, jobStatusLabels } from "@/lib/agent-jobs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { JobLog } from "@/components/job-log";

const statusStyles: Record<JobStatus, string> = {
  running: "text-success", completed: "text-success", waiting: "text-warning", attention: "text-warning",
  failed: "text-danger", rejected: "text-muted-foreground", queued: "text-muted-foreground", recorded: "text-muted-foreground",
};
const statusIcons = { running: LoaderCircle, queued: Clock3, waiting: CirclePause, completed: CircleCheck, failed: CircleX, rejected: CircleX, attention: CirclePause, recorded: CircleDashed };
const filters: { value: JobFilter; label: string }[] = [
  { value: "all", label: "All jobs" }, { value: "active", label: "Active" },
  { value: "needs-attention", label: "Needs attention" }, { value: "finished", label: "Finished" },
];
const date = (value: string) => value.replace("T", " ").replace("Z", " UTC");
const name = (value: string) => value.replaceAll("_", " ");

function StatusIcon({ status }: { status: JobStatus }) {
  const Icon = statusIcons[status];
  return <span title={jobStatusLabels[status]} className={`inline-flex shrink-0 ${statusStyles[status]}`}>
    <Icon className={`size-4 ${status === "running" ? "motion-safe:animate-spin" : ""}`} aria-hidden="true" />
    <span className="sr-only">{jobStatusLabels[status]}</span>
  </span>;
}

export function AgentJobs({ jobs }: { jobs: AgentJob[] }) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<JobFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const previousSelection = useRef<string | null>(null);
  const router = useRouter();
  const [refreshing, refresh] = useTransition();
  useEffect(() => {
    const read = () => {
      const encoded = window.location.hash.startsWith("#agent-jobs/") ? window.location.hash.slice("#agent-jobs/".length) : "";
      try { setSelectedId(encoded ? decodeURIComponent(encoded) : null); } catch { setSelectedId(null); }
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  useEffect(() => {
    if (selectedId) { heading.current?.focus({ preventScroll: true }); window.scrollTo({ top: 0 }); }
    else if (previousSelection.current) document.getElementById(`lantern-job-link-${previousSelection.current}`)?.focus();
    previousSelection.current = selectedId;
  }, [selectedId]);
  const selected = jobs.find(job => job.id === selectedId);
  const visible = filterJobs(jobs, filter, query);

  return <section id="lantern-agent-jobs" className="min-w-0 space-y-6" aria-labelledby="lantern-agent-jobs-title">
    <div className="flex items-center justify-between gap-4">
      <div className="flex min-w-0 items-baseline gap-3"><h1 ref={heading} tabIndex={-1} id="lantern-agent-jobs-title" className="text-xl font-medium tracking-tight outline-none">Agent jobs</h1><span className="text-sm text-muted-foreground">{jobs.length}</span></div>
      <Button id="lantern-jobs-refresh" variant="ghost" size="sm" className="min-h-11 text-muted-foreground" disabled={refreshing} onClick={() => refresh(() => router.refresh())}><RefreshCw aria-hidden="true" className={`size-3.5 ${refreshing ? "motion-safe:animate-spin" : ""}`} />{refreshing ? "Refreshing…" : "Refresh"}</Button>
    </div>

    {selectedId ? <>
      <Button asChild variant="ghost" size="sm" className="min-h-11 -ml-3 text-muted-foreground"><a id="lantern-jobs-back" href="#agent-jobs"><ArrowLeft className="size-4" aria-hidden="true" />All jobs</a></Button>
      {selected ? <JobDetails job={selected} /> : <div role="status" className="space-y-2 border-t py-10"><p className="text-sm font-medium">This job is outside the current list.</p><p className="text-sm text-muted-foreground">Return to the latest jobs or refresh.</p></div>}
    </> : <div>
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b pb-3">
        <div id="lantern-jobs-filters" role="group" aria-label="Filter jobs" className="flex flex-wrap gap-1">
          {filters.map(item => <Button key={item.value} id={`lantern-jobs-filter-${item.value}`} size="sm" variant={filter === item.value ? "secondary" : "ghost"} aria-pressed={filter === item.value} className={`min-h-11 px-3 font-normal ${filter === item.value ? "" : "text-muted-foreground"}`} onClick={() => setFilter(item.value)}>{item.label}</Button>)}
        </div>
        <div className="relative min-w-0 flex-1 basis-48 sm:max-w-64"><Search aria-hidden="true" className="pointer-events-none absolute top-3.5 left-3 size-4 text-muted-foreground" /><Input id="lantern-jobs-search" aria-label="Search agent jobs" placeholder="Search jobs…" value={query} onChange={event => setQuery(event.target.value)} className="h-11 border-transparent bg-transparent pl-9 shadow-none hover:border-border focus-visible:border-input" /></div>
      </div>
      <div id="lantern-jobs-list" className="min-w-0">
        <ul aria-label="Agent jobs" className="divide-y divide-border/60">
          {visible.map(job => <li id={`lantern-job-${job.id}`} key={job.id}>
            <a id={`lantern-job-link-${job.id}`} href={`#agent-jobs/${encodeURIComponent(job.id)}`} title={`${job.id} · ${jobStatusLabels[job.status]} · ${job.title}`} className="flex min-h-14 items-center gap-3 rounded-sm px-2 py-3 transition-colors hover:bg-muted/60 focus-visible:outline-2 focus-visible:outline-ring sm:px-3">
              <span className="hidden w-28 shrink-0 truncate font-mono text-xs text-muted-foreground sm:block">{jobReference(job)}</span>
              <StatusIcon status={job.status} />
              <span className="min-w-0 flex-1 truncate text-sm">{job.title}</span>
              <time dateTime={job.recordedAt} title={date(job.recordedAt)} className="hidden shrink-0 text-xs text-muted-foreground lg:block">{job.recordedAt.slice(11, 16)} UTC</time>
            </a>
          </li>)}
        </ul>
        {visible.length === 0 && <div className="space-y-2 py-14 text-center"><p role="status" className="text-sm">{jobs.length ? "No matching jobs" : "No agent jobs yet"}</p><p className="text-xs text-muted-foreground">{jobs.length ? "Try another search or filter." : "The agent’s work will appear here."}</p>{jobs.length > 0 && <Button id="lantern-jobs-clear" variant="ghost" className="min-h-11" onClick={() => { setQuery(""); setFilter("all"); }}>Clear filters</Button>}</div>}
        <p id="lantern-jobs-result-count" role="status" className="border-t pt-4 text-xs text-muted-foreground">{visible.length} {visible.length === 1 ? "job" : "jobs"}{filter !== "all" || query.trim() ? ` of ${jobs.length}` : ""}</p>
      </div>
    </div>}
  </section>;
}

function JobDetails({ job }: { job: AgentJob }) {
  return <article id="lantern-job-details" className="min-w-0 space-y-7">
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground"><span title={job.id} className="font-mono">{jobReference(job)}</span><span className="inline-flex items-center gap-2"><StatusIcon status={job.status} /><span aria-hidden="true">{jobStatusLabels[job.status]}</span></span>{job.simulated && <span className="text-warning">Simulated</span>}</div>
      <h2 id="lantern-job-details-title" className="break-words text-xl font-medium leading-8">{job.title}</h2>
      <dl className="flex flex-wrap gap-x-6 gap-y-3 text-xs text-muted-foreground">
        <div className="flex gap-2"><dt>Actor</dt><dd className="text-foreground">{job.actor}</dd></div>
        <div className="flex gap-2"><dt>Recorded</dt><dd><time dateTime={job.recordedAt}>{date(job.recordedAt)}</time></dd></div>
        <div className="flex gap-2"><dt>Model time</dt><dd className="text-foreground">{job.usage.calls ? formatModelTime(job.usage.latencyMs) : "Not recorded"}</dd></div>
      </dl>
    </div>
    <div className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <section className="min-w-0 space-y-4 border-t pt-5" aria-labelledby="lantern-job-reasoning-title">
        <h3 id="lantern-job-reasoning-title" className="text-sm font-medium">Reasoning</h3><p className="break-words text-sm leading-7 text-muted-foreground">{job.reason}</p>
        {job.simulated && <p className="text-xs leading-6 text-warning">Execution was simulated. No bank action was performed.</p>}
        {job.approval && <div className="space-y-3 border-t pt-5"><h3 className="text-sm font-medium">Approval request</h3><p className="break-words text-sm leading-7 text-muted-foreground">{job.approval.question}</p><p className="text-xs text-muted-foreground">{job.approval.approver} · {name(job.approval.status)}</p></div>}
      </section>
      <section className="min-w-0 space-y-4 border-t pt-5" aria-labelledby="lantern-job-activity-title">
        <h3 id="lantern-job-activity-title" className="text-sm font-medium">Activity</h3>
        <ul className="space-y-5 text-xs leading-6 text-muted-foreground">
          {job.usage.events.map(event => <li key={event.id}><p>{event.kind ? name(event.kind) : "Trigger event"}</p><p className="break-all font-mono">{event.id}</p></li>)}
          <li>{job.usage.calls ? `${job.usage.calls} model calls · ${job.usage.tokens.toLocaleString("en-US")} ${job.usage.estimated ? "estimated " : ""}tokens` : "No model calls linked to this job."}{job.usage.failedCalls > 0 && <p className="text-warning">{job.usage.failedCalls} model calls failed.</p>}</li>
          <li><p>Job recorded</p><time dateTime={job.recordedAt}>{date(job.recordedAt)}</time></li>
          {job.approval && <li><p>Approval requested</p><time dateTime={job.approval.createdAt}>{date(job.approval.createdAt)}</time>{job.approval.resolvedAt && <p>Resolved <time dateTime={job.approval.resolvedAt}>{date(job.approval.resolvedAt)}</time></p>}</li>}
        </ul>
        <p className="text-xs leading-6 text-muted-foreground">Model time includes linked calls only.</p>
      </section>
    </div>
    <JobLog key={job.id} jobId={job.id} />
  </article>;
}
