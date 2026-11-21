"use client";

import Link from "next/link";
import { tenantFetch, scopeKey } from "@/lib/tenant-browser";
import { useEffect, useState } from "react";
import { FileText, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { TransactionInvoice } from "@/lib/invoice-model";
import { evidenceView, money, type AgentTask, type TransactionRow } from "@/lib/scout-model";

// Invoices do not change once filed: keep the ones opened in this tab.
const invoices = new Map<string, Promise<TransactionInvoice>>();
function loadInvoice(id: string): Promise<TransactionInvoice> {
  let pending = invoices.get(scopeKey(id));
  if (!pending) {
    pending = tenantFetch(`/api/invoices/${encodeURIComponent(id)}`, { cache: "no-store" }).then(async response => {
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof body?.error === "string" ? body.error : "The invoice could not be loaded.");
      return body as TransactionInvoice;
    });
    pending.catch(() => invoices.delete(scopeKey(id)));
    invoices.set(scopeKey(id), pending);
  }
  return pending;
}

export function Transactions({ rows, tasks }: { rows: TransactionRow[]; tasks: AgentTask[] }) {
  const taskFor = new Map(tasks.filter(task => task.transactionId).map(task => [task.transactionId!, task.id]));
  const [open, setOpen] = useState<TransactionRow | null>(null);
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const visible = needle ? rows.filter(row => `${row.counterparty} ${row.reference} ${row.invoiceNumber ?? ""} ${row.category ?? ""}`.toLowerCase().includes(needle)) : rows;
  const documented = rows.filter(row => row.invoiceNumber && row.documented).length;
  const missing = rows.filter(row => row.invoiceNumber && !row.documented).length;

  return <section className="space-y-4">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-2xl font-medium tracking-tight">Transactions</h1>
        <p className="mt-1 text-sm text-muted-foreground">Latest 100 bank bookings from Revolut Business. Click a documented invoice to see it.</p></div>
      <div className="flex items-center gap-2 text-xs">
        <Badge variant="outline" className="border-success-border font-normal text-success">{documented} documented</Badge>
        <Badge variant="outline" className="border-warning-border font-normal text-warning">{missing} missing</Badge>
        <div className="relative"><Search className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted-foreground" aria-hidden="true" />
          <Input id="scout-transactions-search" aria-label="Filter transactions" value={query} onChange={event => setQuery(event.target.value)} placeholder="Filter…" className="h-8 w-48 pl-8 text-[13px]" /></div>
      </div>
    </div>
    <div className="rounded-xl border bg-card"><Table className="text-[13px]">
      <TableHeader><TableRow><TableHead>Date</TableHead><TableHead>Counterparty</TableHead><TableHead>Reference</TableHead><TableHead>Category</TableHead><TableHead>Invoice</TableHead><TableHead>Task</TableHead><TableHead className="text-right">Amount</TableHead></TableRow></TableHeader>
      <TableBody>{visible.map(row => <TableRow key={row.id} data-transaction-id={row.id}>
        <TableCell className="font-mono text-xs whitespace-nowrap">{row.bookingDate}</TableCell>
        <TableCell>{row.counterparty}</TableCell>
        <TableCell className="max-w-56 truncate text-muted-foreground">{row.reference}</TableCell>
        <TableCell>{row.category?.replaceAll("_", " ") ?? "—"}</TableCell>
        <TableCell>{row.invoiceNumber ? row.documented
          ? <button type="button" onClick={() => setOpen(row)} aria-label={`Show invoice ${row.invoiceNumber}`}
              className="inline-flex items-center gap-1 rounded-md border border-success-border px-2 py-0.5 text-xs text-success transition-colors hover:bg-success-muted focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
              <FileText className="size-3" aria-hidden="true" />{row.invoiceNumber} · documented</button>
          : <Badge variant="outline" className="border-warning-border font-normal text-warning">{row.invoiceNumber} · missing</Badge> : "—"}</TableCell>
        <TableCell>{taskFor.has(row.id) ? <Link href={`#task/${encodeURIComponent(taskFor.get(row.id)!)}`} className="font-mono text-xs underline-offset-2 hover:underline">{taskFor.get(row.id)}</Link> : "—"}</TableCell>
        <TableCell className={`text-right font-mono ${row.amount > 0 ? "text-success" : ""}`}>{money(row.amount)}</TableCell>
      </TableRow>)}</TableBody>
    </Table>
      {visible.length === 0 && <p className="px-4 py-8 text-center text-sm text-muted-foreground">No bookings match “{query}”.</p>}
    </div>
    <Sheet open={open !== null} onOpenChange={value => { if (!value) setOpen(null); }}>
      <SheetContent id="scout-invoice-sheet" className="w-full gap-0 overflow-y-auto sm:max-w-xl">
        {open && <InvoicePanel row={open} />}
      </SheetContent>
    </Sheet>
  </section>;
}

function InvoicePanel({ row }: { row: TransactionRow }) {
  const [state, setState] = useState<{ id: string; data?: TransactionInvoice; error?: string }>({ id: row.id });
  useEffect(() => {
    let live = true;
    loadInvoice(row.id).then(data => { if (live) setState({ id: row.id, data }); }, (error: Error) => { if (live) setState({ id: row.id, error: error.message }); });
    return () => { live = false; };
  }, [row.id]);
  const data = state.id === row.id ? state.data : undefined;
  const error = state.id === row.id ? state.error : undefined;
  const evidence = data?.document?.evidence ? evidenceView(data.document.evidence) : null;
  const linesTotal = data?.lines.reduce((sum, line) => sum + Math.round(line.amount * 100), 0);

  return <>
    <SheetHeader className="border-b pr-12">
      <p className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">Invoice</p>
      <SheetTitle className="text-xl font-medium tracking-tight">{data?.invoice.number ?? row.invoiceNumber}</SheetTitle>
      <SheetDescription>{data?.invoice.supplier ?? row.counterparty} · paid {row.bookingDate} · {money(row.amount)}</SheetDescription>
    </SheetHeader>
    <div className="space-y-5 p-4" aria-busy={!data && !error}>
      {error ? <p role="alert" className="text-sm text-danger">{error}</p> : !data ? <InvoiceSkeleton /> : <>
        <dl className="grid grid-cols-2 gap-3 text-[13px]">
          {[["Supplier", data.invoice.supplier], ["Total", money(data.invoice.total, data.invoice.currency)], ["Issued", data.invoice.issueDate], ["Due", data.invoice.dueDate ?? "—"],
            ["Category", data.invoice.category?.replaceAll("_", " ") ?? row.category?.replaceAll("_", " ") ?? "—"], ["Source", data.invoice.source === "db" ? "Invoicing system" : data.invoice.source]]
            .map(([label, value]) => <div key={label} className="rounded-lg border bg-card px-3 py-2"><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className={label === "Total" ? "font-mono text-base" : ""}>{value}</dd></div>)}
        </dl>
        {Math.round(data.invoice.total * 100) !== Math.round(-row.amount * 100) && <p className="rounded-md border border-warning-border bg-warning-muted px-3 py-2 text-xs text-warning">The invoice total differs from the payment ({money(-row.amount)}).</p>}
        <section aria-labelledby="scout-invoice-lines-title" className="overflow-hidden rounded-xl border">
          <h3 id="scout-invoice-lines-title" className="border-b bg-muted/40 px-3 py-2 text-[13px] font-medium">Line items <span className="font-normal text-muted-foreground">{data.lines.length}</span></h3>
          {data.lines.length === 0 ? <p className="px-3 py-4 text-sm text-muted-foreground">No line items recorded.</p> :
            <Table className="text-[13px]"><TableHeader><TableRow><TableHead>Description</TableHead><TableHead className="text-right">Qty</TableHead><TableHead className="text-right">Unit</TableHead><TableHead className="text-right">Amount</TableHead></TableRow></TableHeader>
              <TableBody>{data.lines.map(line => <TableRow key={line.position}><TableCell className="max-w-56 whitespace-normal">{line.description}</TableCell><TableCell className="text-right font-mono text-xs">{line.quantity.toLocaleString("en-US")}</TableCell><TableCell className="text-right font-mono text-xs">{money(line.unitPrice)}</TableCell><TableCell className="text-right font-mono text-xs">{money(line.amount)}</TableCell></TableRow>)}</TableBody>
              <TableFooter><TableRow><TableCell colSpan={3}>Total</TableCell><TableCell className="text-right font-mono text-xs">{money((linesTotal ?? 0) / 100)}</TableCell></TableRow></TableFooter>
            </Table>}
        </section>
        <section aria-labelledby="scout-invoice-document-title" className="space-y-2">
          <h3 id="scout-invoice-document-title" className="text-[13px] font-medium">Document Scout read</h3>
          {data.document ? <>
            <p className="text-xs text-muted-foreground">{evidence ? <>{evidence.label}{evidence.detail ? ` · ${evidence.detail}` : ""} · </> : null}filed by <Link href={`#task/${encodeURIComponent(data.document.taskId)}`} className="font-mono underline-offset-2 hover:underline">{data.document.taskId}</Link></p>
            {data.document.textExcerpt ? <pre className="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-3 font-mono text-[11px] leading-5 whitespace-pre-wrap">{data.document.textExcerpt}</pre>
              : <p className="text-sm text-muted-foreground">No text was extracted from this document.</p>}
          </> : <p className="text-sm text-muted-foreground">Matched in the invoicing system; no separate document was read.{data.taskId && <> Task <Link href={`#task/${encodeURIComponent(data.taskId)}`} className="font-mono underline-offset-2 hover:underline">{data.taskId}</Link>.</>}</p>}
        </section>
      </>}
    </div>
  </>;
}

function InvoiceSkeleton() {
  return <div className="space-y-5" role="status" aria-label="Loading invoice">
    <div className="grid grid-cols-2 gap-3">{[0, 1, 2, 3, 4, 5].map(cell => <Skeleton key={cell} className="h-14 rounded-lg" />)}</div>
    <div className="space-y-2 rounded-xl border p-3">{[0, 1, 2, 3].map(row => <Skeleton key={row} className="h-5" />)}</div>
    <Skeleton className="h-32 rounded-lg" />
  </div>;
}
