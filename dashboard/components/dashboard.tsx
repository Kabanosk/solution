"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Activity, ArrowDownLeft, ArrowUpRight, Bell, CalendarClock, Inbox, LayoutGrid, ListTodo, Plug, Search, ShieldCheck, Wallet } from "lucide-react";
import type { DashboardData } from "@/lib/finance";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SidebarInset, SidebarProvider, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { FinancialCharts } from "@/components/financial-charts";
import { Staffing } from "@/components/staffing";
import { DomIds } from "@/components/dom-ids";
import { AppSidebar, type NavCounts } from "@/components/app-sidebar";
import { TaskList } from "@/components/tasks/task-list";
import { TaskDetailView } from "@/components/tasks/task-detail";
import { AuditLog } from "@/components/audit-log";
import { Connections } from "@/components/connections";
import { AccessPolicies } from "@/components/access-policies";
import { DemoPanel } from "@/components/demo-panel";
import { Transactions } from "@/components/transactions";
import { GO_SHORTCUTS, navItem, viewFromHash, type View } from "@/lib/nav";
import { formatShare, isOpen, money, shortDate, type AgentTask, type TaskDetail } from "@/lib/scout-model";

const currency = (amount: number) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format(amount);
const compact = (amount: number) => amount >= 1000 ? `€${(amount / 1000).toFixed(1)}k` : currency(amount);
const mobileNavigation: { view: View; label: string; icon: typeof Inbox }[] = [
  { view: "inbox", label: "Inbox", icon: Inbox }, { view: "tasks", label: "Tasks", icon: ListTodo },
  { view: "overview", label: "Overview", icon: LayoutGrid }, { view: "connections", label: "Connections", icon: Plug },
];
const LIVE = (task: AgentTask) => task.status === "in_progress" || task.status === "waiting";

export function Dashboard({ data, detail }: { data: DashboardData; detail?: TaskDetail }) {
  // Assign IDs only after this client tree hydrates, rather than from the
  // layout while the streamed dashboard is still waiting to hydrate.
  return <SidebarProvider><DashboardContent data={data} detail={detail} /><DomIds /></SidebarProvider>;
}

function DashboardContent({ data, detail }: { data: DashboardData; detail?: TaskDetail }) {
  const { setOpenMobile } = useSidebar();
  const [page, setPage] = useState<View>(detail ? "tasks" : "overview");
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const { scout } = data;
  const ceo = scout.ceoId;
  const names = useMemo(() => Object.fromEntries(data.employees.map(person => [person.id, person.name])), [data.employees]);
  const openTasks = scout.tasks.filter(isOpen);
  const inboxTasks = openTasks.filter(task => (task.status === "waiting" || task.status === "escalated") && (task.ownerId === ceo || task.pendingApprovalFor === ceo));
  const myTasks = scout.tasks.filter(task => task.ownerId === ceo);
  const waiting = scout.tasks.filter(task => task.status === "waiting").length;
  const escalated = scout.tasks.filter(task => task.status === "escalated").length;
  const attention = waiting + escalated;
  const counts: NavCounts = {
    inbox: inboxTasks.length, myTasks: myTasks.filter(isOpen).length, openTasks: openTasks.length,
    loops: Object.fromEntries(["invoicing", "cost_control", "liquidity", "chat"].map(loop => [loop, openTasks.filter(task => task.loop === loop).length])),
  };
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [notice, setNoticeState] = useState<{ title: string; description: string } | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const searchOrigin = useRef<HTMLElement | null>(null);
  const noticeOrigin = useRef<HTMLElement | null>(null);
  const searchNavigated = useRef(false);
  const openSearch = () => { searchOrigin.current = document.activeElement as HTMLElement; searchNavigated.current = false; setSearchOpen(true); };
  const setNotice = (value: { title: string; description: string } | null) => { if(value) noticeOrigin.current = document.activeElement as HTMLElement; setNoticeState(value); };

  const navigate = (next: View) => {
    searchNavigated.current = searchOpen;
    setOpenMobile(false);
    setSearchOpen(false);
    if (detail) { router.push(`/#${next}`); return; }
    setPage(next);
    requestAnimationFrame(() => mainRef.current?.focus());
    if (window.location.hash !== `#${next}`) window.location.hash = next;
  };
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  useEffect(() => {
    const fromHash = () => { if (!detail) setPage(viewFromHash(window.location.hash)); };
    let lastG = 0;
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchOrigin.current = document.activeElement as HTMLElement;
        searchNavigated.current = false;
        setSearchOpen((open) => !open);
        return;
      }
      const target = event.target as HTMLElement | null;
      if (event.metaKey || event.ctrlKey || event.altKey || target?.closest("input, textarea, select, [contenteditable=true]")) return;
      const key = event.key.toLowerCase();
      if (key === "g") { lastG = Date.now(); return; }
      if (Date.now() - lastG < 1200 && GO_SHORTCUTS[key]) { lastG = 0; event.preventDefault(); navigateRef.current(GO_SHORTCUTS[key]); }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    window.addEventListener("keydown", shortcut);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      window.removeEventListener("keydown", shortcut);
    };
  }, [detail]);

  useEffect(() => { mainRef.current?.focus({ preventScroll: true }); }, [page]);

  // Tasks visible in the current view; poll while any of them is still moving.
  const loopFilter = navItem(page)?.loop;
  const visibleTasks = detail ? [detail.task]
    : page === "inbox" ? inboxTasks : page === "my-tasks" ? myTasks : page === "tasks" ? scout.tasks
    : loopFilter ? scout.tasks.filter(task => task.loop === loopFilter) : page === "overview" ? openTasks : [];
  const live = visibleTasks.some(LIVE);
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(timer);
  }, [live, router]);

  const searchItems = [
    { label: "Agent tasks", detail: `${openTasks.length} open tasks`, view: "tasks" as View, href: undefined as string | undefined },
    { label: "Overview", detail: "Cash balance, forecast and expense mix", view: "overview" as View, href: undefined },
    { label: "Connections", detail: "Bank, invoicing, email, Slack and laptops", view: "connections" as View, href: undefined },
    { label: "Audit log", detail: "ALLOW / DENY decisions", view: "audit" as View, href: undefined },
    { label: "Staffing & payroll", detail: "Team, salaries and payroll export", view: "staffing" as View, href: undefined },
    ...scout.tasks.map(task => ({ label: `${task.id} · ${task.title}`, detail: `${task.status.replace("_", " ")}${task.counterparty ? ` · ${task.counterparty}` : ""}`, view: "tasks" as View, href: `/tasks/${encodeURIComponent(task.id)}` })),
    ...data.employees.map((person) => ({ label: person.name, detail: `${person.role} · ${person.team}`, view: "staffing" as View, href: undefined })),
  ].filter((item) => `${item.label} ${item.detail}`.toLowerCase().includes(search.toLowerCase().trim())).slice(0, 40);

  const title = detail ? `Agent tasks › ${detail.task.id}` : navItem(page)?.label ?? "Overview";
  const ceoName = names[ceo] ?? "CEO";

  let content: React.ReactNode;
  if (detail) content = <TaskDetailView detail={detail} names={names} />;
  else if (page === "inbox") content = <TaskList key={page} tasks={inboxTasks} names={names} title="Inbox" description={`Waiting on or escalated to ${ceoName}, including pending approvals.`} filters={false} />;
  else if (page === "my-tasks") content = <TaskList key={page} tasks={myTasks} names={names} title="My tasks" description={`Tasks owned by ${ceoName}.`} />;
  else if (page === "tasks" || loopFilter) content = <div className="space-y-4">
    {scout.demo && <DemoPanel />}
    <TaskList key={page} tasks={scout.tasks} names={names} title={loopFilter ? `${navItem(page).label} loop` : "Agent tasks"} initial={loopFilter ? { loop: loopFilter } : {}} />
  </div>;
  else if (page === "staffing") content = <Staffing data={data} onBack={() => navigate("overview")} />;
  else if (page === "transactions") content = <Transactions rows={scout.transactions} tasks={scout.tasks} />;
  else if (page === "forecast") content = <div className="space-y-6">
    <div><h1 className="text-2xl font-medium tracking-tight">Forecast</h1><p className="mt-1 text-sm text-muted-foreground">90-day liquidity forecast and the cash events Scout expects.</p></div>
    <FinancialCharts data={data} /><CashEvents data={data} />
  </div>;
  else if (page === "connections") content = <Connections connections={scout.connections} />;
  else if (page === "audit") content = <AuditLog rows={scout.audit} />;
  else if (page === "policies") content = <AccessPolicies />;
  else content = <div id="scout-overview" className="space-y-6">
    <section className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
      <div className="space-y-2"><p className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">Scout · Your autonomous CFO</p><h1 className="text-3xl font-medium tracking-tight">Your finance overview</h1><p className="max-w-2xl text-sm leading-6 text-muted-foreground">{data.year.transactions.toLocaleString("en-US")} bank transactions. {attention} tasks wait on people. Every outcome connected to its evidence.</p></div>
      <Button id="scout-refresh" className="min-h-11 shrink-0 gap-2" disabled={refreshing} onClick={() => startRefresh(() => router.refresh())}><Activity className="size-4" aria-hidden="true" />{refreshing ? "Refreshing…" : "Refresh records"}</Button>
    </section>

    {scout.demo && <DemoPanel />}

    <section id="scout-financial-summary" aria-label="Financial summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      {[
        { label: "Available balance", value: currency(data.closingBalance), note: `As of ${data.asOf}`, trend: "Sum of recorded bank bookings", icon: Wallet, tone: "positive" },
        { label: "Money in", value: compact(data.year.totalRevenue), note: `Recorded inflows · ${data.yearLabel}`, trend: "Company bank transactions", icon: ArrowDownLeft, tone: "neutral" },
        { label: "Money out", value: compact(data.year.totalExpenses), note: `Recorded outflows · ${data.yearLabel}`, trend: `${data.year.expenses.length} categories tracked`, icon: ArrowUpRight, tone: "neutral" },
        { label: "Needs attention", value: String(attention), note: "Waiting + escalated tasks", trend: `${escalated} escalated`, icon: Activity, tone: "warning" },
        { label: "Explained by Scout", value: `${scout.explained.documented}/${scout.explained.total}`, note: `${formatShare(scout.explained.documented, scout.explained.total)} of payments`, trend: "Outgoing payments with an invoice reference", icon: ShieldCheck, tone: "positive" },
      ].map(({ label, value, note, trend, icon: Icon, tone }) => (
        <Card key={label} className="gap-3 py-5 shadow-none">
          <CardContent className="space-y-3 px-4">
            <div className="flex items-center justify-between gap-3"><span className={`flex size-8 items-center justify-center rounded-md border ${tone === "positive" ? "border-success-border text-success" : tone === "warning" ? "border-warning-border text-warning" : "text-muted-foreground"}`}><Icon className="size-4" aria-hidden="true" /></span><p className="text-xs text-muted-foreground">{label}</p></div>
            <div><strong className="block font-mono text-[1.65rem] font-medium tracking-tighter">{value}</strong><span className="mt-1 block text-[11px] text-muted-foreground">{note}</span></div>
            <p className={`pt-1 text-[11px] ${tone === "positive" ? "text-success" : tone === "warning" ? "text-warning" : "text-muted-foreground"}`}>{trend}</p>
          </CardContent>
        </Card>
      ))}
    </section>

    <FinancialCharts data={data} />

    <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]" aria-label="Cash events and agent tasks">
      <CashEvents data={data} />
      <TaskList tasks={openTasks} names={names} title="Open agent tasks" filters={false} />
    </section>

    <Card id="scout-priority-inbox" aria-labelledby="priority-title" className="gap-5">
      <CardHeader className="flex flex-row justify-between gap-4"><div className="space-y-1.5"><CardTitle id="priority-title" className="text-base font-medium">Priority inbox</CardTitle><CardDescription>What Scout could not settle on its own</CardDescription></div><Badge variant="outline" className="h-fit shrink-0 border-warning-border text-warning">{attention} open</Badge></CardHeader>
      <CardContent className="divide-y">
        <PriorityItem label="Escalated" title={`${escalated} escalated tasks`} description="Nobody answered Scout's questions in time; the task moved up to the CEO." action="Open inbox" tone="red" onClick={() => navigate("inbox")} />
        <PriorityItem label="Waiting" title={`${waiting} tasks waiting on people`} description="Scout asked someone on Slack or is waiting for an approval." action="View tasks" tone="orange" onClick={() => navigate("tasks")} />
        <PriorityItem label="Documents" title={`${data.undocumentedPayments} payments without invoice documents`} description="Bank bookings with an invoice reference that are not documented yet." action="Transactions" tone="neutral" onClick={() => navigate("transactions")} />
      </CardContent>
    </Card>

    <Card id="scout-recorded-payroll" className="gap-4"><CardHeader><CardTitle className="text-base font-medium">Latest recorded payroll</CardTitle><CardDescription>Latest month with salary bookings</CardDescription></CardHeader><CardContent className="space-y-4"><div className="flex items-center justify-between gap-3"><div><span className="text-xs text-muted-foreground">{data.employees.filter(employee => employee.salary !== null).length} recorded recipients</span><strong className="mt-1 block font-mono text-2xl font-medium tracking-tight">{currency(data.monthlyPayroll)}</strong></div><div className="flex -space-x-2" aria-label="Team members">{data.employees.map(employee=><Avatar key={employee.id} className="size-8 border-2 border-card"><AvatarFallback className="bg-secondary text-[10px]">{employee.initials}</AvatarFallback></Avatar>)}</div></div><Button variant="secondary" size="sm" className="min-h-10" onClick={() => navigate("staffing")}>Review staffing</Button></CardContent></Card>
  </div>;

  return (
    <>
      <a id="scout-skip-link" href="#main-content" onClick={event=>{event.preventDefault();mainRef.current?.focus();}} className="fixed top-3 left-3 z-50 -translate-y-24 rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground focus:translate-y-0">Skip to content</a>
      <AppSidebar active={page} counts={counts} user={{ name: ceoName, role: "CEO · dashboard login" }} onNavigate={navigate} onSearch={openSearch}
        onNewTask={() => setNotice({ title: "New task", description: "Scout opens tasks from its loops. To ask for something new, DM Scout in Slack, e.g. “what if I buy an oven for 30k in March?” — it appears here under Agent tasks." })} />

      <SidebarInset id="scout-workspace" className="min-w-0 bg-background">
        <header id="scout-topbar" className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur-sm md:px-6">
          <SidebarTrigger aria-label="Toggle navigation" />
          <div className="flex min-w-0 flex-1 items-center gap-2 text-[13px]">
            <span className="hidden text-muted-foreground lg:inline">Pizzeria Amalfi</span><span className="hidden text-muted-foreground lg:inline" aria-hidden="true">/</span>
            <strong className="truncate font-medium">{title}</strong>
          </div>
          <Button id="scout-search-trigger" variant="outline" onClick={openSearch} className="hidden h-8 w-full max-w-xs justify-start gap-2 bg-card text-muted-foreground lg:flex">
            <Search className="size-4" aria-hidden="true" /><span className="flex-1 text-left text-xs font-normal">Search tasks, people…</span><kbd className="rounded border px-1.5 py-0.5 font-mono text-[10px]">⌘ K</kbd>
          </Button>
          <Button id="scout-search-compact" variant="ghost" size="icon" className="size-8 shrink-0 lg:hidden" aria-label="Search workspace" onClick={openSearch}><Search className="size-4" aria-hidden="true" /></Button>
          <span className="hidden items-center gap-2 text-[11px] text-muted-foreground xl:flex"><span className={`size-1.5 rounded-full ${live ? "animate-pulse bg-emerald-500" : "bg-emerald-500"}`} aria-hidden="true" />{live ? "Live · refreshing" : "Records through " + data.asOf}</span>
          <Button id="scout-notifications" variant="ghost" size="icon" aria-label="Notifications" className="size-8 shrink-0" onClick={() => navigate("inbox")}><Bell className="size-4" aria-hidden="true" /></Button>
        </header>

        <section id="main-content" ref={mainRef} tabIndex={-1} aria-label={title} className="mx-auto w-full max-w-[1600px] min-w-0 px-4 pt-6 pb-28 outline-none md:px-8 md:pt-7 md:pb-10">
          {content}
        </section>

        <nav id="scout-mobile-navigation" aria-label="Mobile navigation" className="fixed right-0 bottom-0 left-0 z-30 grid grid-cols-4 gap-2 border-t bg-sidebar/95 px-3 pt-2 pb-[max(.5rem,env(safe-area-inset-bottom))] backdrop-blur-sm md:hidden">
          {mobileNavigation.map(({view:target,label,icon:Icon})=><Button key={target} id={`scout-mobile-${target}`} variant={page===target?"secondary":"ghost"} aria-current={page===target?"page":undefined} onClick={()=>navigate(target)} className="h-auto min-h-14 flex-col gap-1 text-[11px]"><Icon className="size-4" aria-hidden="true" />{label}</Button>)}
        </nav>
      </SidebarInset>

      <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
        <DialogContent id="scout-search-dialog" onCloseAutoFocus={event=>{event.preventDefault();if(searchNavigated.current){mainRef.current?.focus();searchNavigated.current=false;}else{searchOrigin.current?.focus();}}} className="max-w-xl"><DialogHeader><DialogTitle>Search your workspace</DialogTitle><DialogDescription>Find views, agent tasks and people.</DialogDescription></DialogHeader><Input id="scout-search-input" aria-label="Search workspace" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Search tasks, people…" /><div className="max-h-80 space-y-1 overflow-y-auto" aria-live="polite">{searchItems.length ? searchItems.map(item=><Button key={item.label} variant="ghost" className="h-auto min-h-14 w-full justify-start px-3 py-2.5 text-left" onClick={()=>{ if (item.href) { setSearchOpen(false); router.push(item.href); } else navigate(item.view); }}><span><span className="block text-sm">{item.label}</span><span className="mt-1 block whitespace-normal text-xs font-normal text-muted-foreground">{item.detail}</span></span></Button>):<p className="p-3 text-sm text-muted-foreground">No matching records.</p>}</div></DialogContent>
      </Dialog>
      <Dialog open={notice !== null} onOpenChange={open=>{if(!open)setNotice(null);}}><DialogContent onCloseAutoFocus={event=>{event.preventDefault();noticeOrigin.current?.focus();}}><DialogHeader><DialogTitle>{notice?.title}</DialogTitle><DialogDescription className="pt-2 leading-7">{notice?.description}</DialogDescription></DialogHeader><Button variant="secondary" onClick={()=>setNotice(null)}>Close</Button></DialogContent></Dialog>
    </>
  );
}

function CashEvents({ data }: { data: DashboardData }) {
  const events = data.scout.cashEvents;
  return <section aria-labelledby="cash-events-title" className="h-fit overflow-hidden rounded-xl border bg-card">
    <h2 id="cash-events-title" className="flex items-center gap-2 border-b px-4 py-2.5 text-[13px] font-medium"><CalendarClock className="size-4 text-muted-foreground" aria-hidden="true" />Upcoming cash events</h2>
    {events.length === 0 ? <p className="px-4 py-6 text-sm text-muted-foreground">No expected cash events.</p> :
      <ul>{events.map(event => <li key={event.id} className="flex items-center gap-4 border-b px-4 py-2.5 text-[13px] last:border-b-0">
        <span className="w-14 shrink-0 font-mono text-xs text-muted-foreground">{shortDate(`${event.onDate}T12:00:00Z`)}</span>
        <span className="min-w-0 flex-1 truncate">{event.label}{event.taskId && <> · <Link href={`/tasks/${encodeURIComponent(event.taskId)}`} className="font-mono text-xs text-muted-foreground hover:underline">{event.taskId}</Link></>}</span>
        <span className={`shrink-0 font-mono text-xs ${event.amount > 0 ? "text-success" : ""}`}>{event.amount > 0 ? "+" : ""}{money(event.amount)}</span>
      </li>)}</ul>}
  </section>;
}

function PriorityItem({ label, title, description, action, tone, onClick }: {label:string;title:string;description:string;action:string;tone:"red"|"orange"|"neutral";onClick:()=>void}) {
  return <article className="flex flex-col justify-between gap-4 py-5 first:pt-0 last:pb-0 sm:flex-row sm:items-center"><div className="space-y-2"><Badge variant="outline" className={`font-normal ${tone==="red"?"border-danger-border text-danger":tone==="orange"?"border-warning-border text-warning":"text-muted-foreground"}`}>{label}</Badge><h3 className="text-sm font-medium">{title}</h3><p className="max-w-3xl text-xs leading-6 text-muted-foreground">{description}</p></div><Button variant="outline" size="sm" className="min-h-10 w-fit shrink-0" onClick={onClick}>{action}</Button></article>;
}
