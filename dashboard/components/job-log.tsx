"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { parseJobLog, type JobLogEntry } from "@/lib/agent-jobs";
import { Button } from "@/components/ui/button";

export function JobLog({ jobId }: { jobId: string }) {
  const [entries, setEntries] = useState<JobLogEntry[] | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    fetch(`/api/jobs/${encodeURIComponent(jobId)}/log`, { cache: "no-store", signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error("Log unavailable");
        const body = await response.json();
        if (!Array.isArray(body.entries)) throw new Error("Invalid log response");
        if (!controller.signal.aborted) setEntries(parseJobLog(body.entries));
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [jobId, revision]);

  return <section id="lantern-job-log" aria-labelledby="lantern-job-log-title" className="min-w-0 space-y-3 border-t pt-5">
    <div className="flex items-center justify-between gap-4">
      <h3 id="lantern-job-log-title" className="text-sm font-medium">Job log</h3>
      <Button id="lantern-job-log-refresh" variant="ghost" size="sm" className="min-h-11 text-muted-foreground" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw aria-hidden="true" className={`size-3.5 ${loading ? "motion-safe:animate-spin" : ""}`} />{loading ? "Loading…" : error ? "Retry" : "Refresh log"}</Button>
    </div>
    <p role="status" className="text-xs text-muted-foreground">{loading ? "Loading job log…" : error ? "Could not load the job log. Try again." : entries?.length ? `${entries.length} entries · UTC · Oldest first` : "No log recorded for this job."}</p>
    {!error && entries && entries.length > 0 && <div id="lantern-job-log-output" role="region" aria-label="Job log entries" aria-busy={loading} tabIndex={0} className="max-h-[28rem] overflow-auto rounded-md border bg-muted/25 focus-visible:outline-2 focus-visible:outline-ring">
      <ol className="divide-y divide-border/40 font-mono text-xs leading-6">
        {entries.map((entry, index) => <li key={entry.id ?? `${entry.ts}-${index}`} className="grid min-w-0 gap-x-4 gap-y-1 px-4 py-2 sm:grid-cols-[11rem_3.5rem_minmax(0,1fr)]">
          <time dateTime={entry.ts} className="text-muted-foreground">{entry.ts.replace("T", " ").replace("Z", "")}</time>
          <span className={`uppercase ${entry.level === "error" ? "text-danger" : entry.level === "warning" ? "text-warning" : "text-muted-foreground"}`}>{entry.level}</span>
          <span className="min-w-0 whitespace-pre-wrap break-words"><span className="text-muted-foreground">[{entry.source}] </span>{entry.message}</span>
        </li>)}
      </ol>
    </div>}
  </section>;
}
