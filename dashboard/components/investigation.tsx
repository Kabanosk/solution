"use client";

import type { DashboardData } from "@/lib/finance-model";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export function Investigation({ data }: { data: DashboardData }) {
  return <section id="lantern-investigations" className="space-y-6">
    <div id="lantern-investigations-heading" className="space-y-2">
      <h1 id="lantern-investigations-title" className="text-3xl font-medium tracking-tight">Agent decisions</h1>
      <p id="lantern-investigations-description" className="text-sm text-muted-foreground">Latest 50 recorded decisions, newest first. Approvals are handled through the agent’s configured channels.</p>
    </div>
    {data.decisions.length === 0 ? <Card id="lantern-decisions-empty"><CardContent id="lantern-decisions-empty-message" className="pt-6" role="status">No agent decisions have been recorded yet.</CardContent></Card> : data.decisions.map(decision => <Card id={`lantern-decision-${decision.id}`} key={decision.id}>
      <CardHeader id={`lantern-decision-heading-${decision.id}`}>
        <div id={`lantern-decision-status-${decision.id}`} className="flex flex-wrap gap-2"><Badge id={`lantern-decision-level-${decision.id}`} variant="outline">{decision.level}</Badge><Badge id={`lantern-decision-state-${decision.id}`} variant="secondary">{decision.status}</Badge>{decision.executed_simulated && <Badge id={`lantern-decision-simulated-${decision.id}`} variant="outline">SIMULATED</Badge>}</div>
        <CardTitle id={`lantern-decision-title-${decision.id}`} className="text-base leading-7">{decision.summary}</CardTitle>
        <CardDescription id={`lantern-decision-date-${decision.id}`}><time id={`lantern-decision-time-${decision.id}`} dateTime={decision.ts}>{decision.ts.replace("T", " ").replace("Z", " UTC")}</time></CardDescription>
      </CardHeader>
      <CardContent id={`lantern-decision-content-${decision.id}`}><p id={`lantern-decision-reason-${decision.id}`} className="text-sm leading-7 text-muted-foreground">{decision.reason}</p></CardContent>
    </Card>)}
  </section>;
}
