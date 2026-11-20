import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { money, type AgentTask, type TransactionRow } from "@/lib/scout-model";

export function Transactions({ rows, tasks }: { rows: TransactionRow[]; tasks: AgentTask[] }) {
  const taskFor = new Map(tasks.filter(task => task.transactionId).map(task => [task.transactionId!, task.id]));
  return <section className="space-y-4">
    <div><h1 className="text-2xl font-medium tracking-tight">Transactions</h1>
      <p className="mt-1 text-sm text-muted-foreground">Latest 100 bank bookings from Revolut Business, with their invoice status.</p></div>
    <div className="rounded-xl border bg-card"><Table className="text-[13px]">
      <TableHeader><TableRow><TableHead>Date</TableHead><TableHead>Counterparty</TableHead><TableHead>Reference</TableHead><TableHead>Category</TableHead><TableHead>Invoice</TableHead><TableHead>Task</TableHead><TableHead className="text-right">Amount</TableHead></TableRow></TableHeader>
      <TableBody>{rows.map(row => <TableRow key={row.id}>
        <TableCell className="font-mono text-xs whitespace-nowrap">{row.bookingDate}</TableCell>
        <TableCell>{row.counterparty}</TableCell>
        <TableCell className="max-w-56 truncate text-muted-foreground">{row.reference}</TableCell>
        <TableCell>{row.category?.replaceAll("_", " ") ?? "—"}</TableCell>
        <TableCell>{row.invoiceNumber ? <Badge variant="outline" className={row.documented ? "border-success-border font-normal text-success" : "border-warning-border font-normal text-warning"}>{row.invoiceNumber} · {row.documented ? "documented" : "missing"}</Badge> : "—"}</TableCell>
        <TableCell>{taskFor.has(row.id) ? <Link href={`/tasks/${encodeURIComponent(taskFor.get(row.id)!)}`} className="font-mono text-xs underline-offset-2 hover:underline">{taskFor.get(row.id)}</Link> : "—"}</TableCell>
        <TableCell className={`text-right font-mono ${row.amount > 0 ? "text-success" : ""}`}>{money(row.amount)}</TableCell>
      </TableRow>)}</TableBody>
    </Table></div>
  </section>;
}
