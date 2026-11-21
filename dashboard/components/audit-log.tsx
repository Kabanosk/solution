"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { clock, fullTimestamp, type AuditRow } from "@/lib/scout-model";
import { cn } from "@/lib/utils";

export function DecisionBadge({ decision }: { decision: "ALLOW" | "DENY" | "MASK" | "BLOCK" }) {
  const tone = decision === "DENY" || decision === "BLOCK" ? "border-danger-border text-danger" : decision === "MASK" ? "border-warning-border text-warning" : "border-success-border text-success";
  return <Badge variant="outline" className={cn("font-mono text-[10px]", tone)}>{decision}</Badge>;
}

export function AuditTable({ rows, compact = false }: { rows: AuditRow[]; compact?: boolean }) {
  if (!rows.length) return <p className="px-4 py-6 text-sm text-muted-foreground">No audit entries.</p>;
  return <Table className="text-[13px]">
    <TableHeader><TableRow>
      <TableHead>Time</TableHead>{!compact && <TableHead>Actor</TableHead>}<TableHead>Requester / role</TableHead><TableHead>Action</TableHead>
      <TableHead>Target</TableHead><TableHead>Resource</TableHead><TableHead>Decision</TableHead><TableHead>Policy</TableHead>
      {!compact && <TableHead>Task</TableHead>}
    </TableRow></TableHeader>
    <TableBody>{rows.map(row => <TableRow key={row.id} title={row.reason ?? undefined}>
      <TableCell className="font-mono text-xs whitespace-nowrap text-muted-foreground" title={fullTimestamp(row.ts)}>{compact ? clock(row.ts) : fullTimestamp(row.ts).replace(" (Rome)", "")}</TableCell>
      {!compact && <TableCell>{row.actor}</TableCell>}
      <TableCell>{row.requester ?? "—"}{row.role && <span className="block text-[11px] text-muted-foreground">{row.role}</span>}</TableCell>
      <TableCell className="font-mono text-xs">{row.action}</TableCell>
      <TableCell>{row.target}</TableCell>
      <TableCell className="max-w-48 truncate">{row.resource}</TableCell>
      <TableCell><DecisionBadge decision={row.decision} /></TableCell>
      <TableCell className="font-mono text-xs text-muted-foreground">{row.policy ?? "—"}</TableCell>
      {!compact && <TableCell>{row.taskId ? <Link href={`#task/${encodeURIComponent(row.taskId)}`} className="font-mono text-xs underline-offset-2 hover:underline">{row.taskId}</Link> : "—"}</TableCell>}
    </TableRow>)}</TableBody>
  </Table>;
}

export function AuditLog({ rows }: { rows: AuditRow[] }) {
  const [denyOnly, setDenyOnly] = useState(false);
  const shown = denyOnly ? rows.filter(row => row.decision === "DENY" || row.decision === "BLOCK") : rows;
  return <section className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-2xl font-medium tracking-tight">Audit log</h1>
        <p className="mt-1 text-sm text-muted-foreground">Every connector read, DM and access-policy decision, newest first.</p></div>
      <Button variant={denyOnly ? "default" : "outline"} size="sm" aria-pressed={denyOnly} onClick={() => setDenyOnly(!denyOnly)}>DENY / BLOCK only</Button>
    </div>
    <div className="rounded-xl border bg-card"><AuditTable rows={shown} /></div>
  </section>;
}
