import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

const POLICIES: { role: string; who: string; rules: [string, "ALLOW" | "DENY" | "APPROVAL"][] }[] = [
  { role: "CEO", who: "Organization approval owner", rules: [
    ["Ask Scout about any company data through read-only connectors", "ALLOW"],
    ["Approve or reject APPROVAL-level proposals (credit lines, large payments)", "APPROVAL"],
    ["Set policy rules for Scout by DM", "ALLOW"],
  ] },
  { role: "Office manager", who: "Designated document owner", rules: [
    ["Receives invoice questions and filing notices", "ALLOW"],
    ["Her own laptop folders (Downloads, Invoices) are searchable by the invoicing loop", "ALLOW"],
    ["Payroll mail label", "ALLOW"],
  ] },
  { role: "Team owners", who: "Designated team owners", rules: [
    ["Receives cost-control alerts and questions for her team's payments", "ALLOW"],
    ["Company mailbox and invoices", "ALLOW"],
    ["Other people's laptops", "DENY"],
  ] },
  { role: "Temporary staff", who: "Restricted staff", rules: [
    ["Ask about the marketing budget she owns", "ALLOW"],
    ["Company mailbox and invoices", "ALLOW"],
    ["Request files from another person's laptop", "DENY"],
    ["Bank or payroll data", "DENY"],
  ] },
  { role: "Scout agent", who: "all loops", rules: [
    ["Read bank, invoicing, authorized files, the organization’s configured mail label and allowlisted Slack channels", "ALLOW"],
    ["File a found document that matches the payment (AUTO)", "ALLOW"],
    ["Payments outside policy add a NOTIFY or APPROVAL step; money is never moved (SIMULATED)", "APPROVAL"],
    ["Answer messages in channels; channel history is only searched for context", "DENY"],
  ] },
];

export function AccessPolicies() {
  return <section className="space-y-4">
    <div><h1 className="text-2xl font-medium tracking-tight">Access policies</h1>
      <p className="mt-1 text-sm text-muted-foreground">Read-only. Scout checks these before any connector reads or acts; every decision is written to the audit log.</p></div>
    <div className="grid gap-4 lg:grid-cols-2">{POLICIES.map(policy => <Card key={policy.role} className="gap-3 shadow-none">
      <CardHeader><CardTitle className="text-sm font-medium">{policy.role}</CardTitle><CardDescription>{policy.who}</CardDescription></CardHeader>
      <CardContent><ul className="space-y-2">{policy.rules.map(([rule, effect]) => <li key={rule} className="flex items-start justify-between gap-3 text-[13px]">
        <span>{rule}</span>
        <Badge variant="outline" className={effect === "DENY" ? "border-danger-border font-mono text-[10px] text-danger" : effect === "APPROVAL" ? "border-warning-border font-mono text-[10px] text-warning" : "border-success-border font-mono text-[10px] text-success"}>{effect}</Badge>
      </li>)}</ul></CardContent>
    </Card>)}</div>
  </section>;
}
