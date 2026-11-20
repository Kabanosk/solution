"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Activity, ArrowDownLeft, ArrowUpRight, Bell, CalendarClock, Inbox, LayoutGrid, ListTodo, MessageSquare, Search, ShieldCheck, Wallet } from "lucide-react";
import Link from "next/link";
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
import { AttentionList, TaskBoard } from "@/components/tasks/task-list";
import { TaskDetailSkeleton, TaskDetailView } from "@/components/tasks/task-detail";
import { AuditLog } from "@/components/audit-log";
import { Connections } from "@/components/connections";
import { AccessPolicies } from "@/components/access-policies";
import { DemoPanel } from "@/components/demo-panel";
import { Transactions } from "@/components/transactions";
import { LlmChat } from "@/components/llm-chat";
import { LlmUsage } from "@/components/llm-usage";
import { ThemeToggle } from "@/components/theme-toggle";
import { activeItem, GO_SHORTCUTS, hashFromRoute, routeFromHash, VIEW_LABEL, type Route, type TaskFilter, type View } from "@/lib/nav";
import { formatShare, isOpen, LOOP_LABEL, money, shortDate, type AgentTask, type TaskDetail } from "@/lib/scout-model";
import { inInbox, loopSignal, needsReaction } from "@/lib/task-signals";
import { useSeenTasks } from "@/lib/use-seen-tasks";
import { cn } from "@/lib/utils";

const currency = (amount: number) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format(amount);
const compact = (amount: number) => amount >= 1000 ? `€${(amount / 1000).toFixed(1)}k` : currency(amount);
const mobileNavigation: { route: Route; id: string; label: string; icon: typeof Inbox }[] = [
  { route: { view: "overview" }, id: "overview", label: "Overview", icon: LayoutGrid },
  { route: { view: "tasks", filter: { scope: "inbox" } }, id: "inbox", label: "Inbox", icon: Inbox },
  { route: { view: "tasks" }, id: "tasks", label: "Tasks", icon: ListTodo },
  { route: { view: "chat" }, id: "chat", label: "Chat", icon: MessageSquare },
];
const LIVE = (task: AgentTask) => task.status === "in_progress" || task.status === "waiting";
const LOOPS = ["invoicing", "cost_control", "liquidity", "chat"];

// Task details opened in this tab, so going back to a task is instant (refreshed in the background).
const detailCache = new Map<string, TaskDetail>();

export function Dashboard({ data }: { data: DashboardData }) {
  // Assign IDs only after this client tree hydrates, rather than from the
  // layout while the streamed dashboard is still waiting to hydrate.
  return <SidebarProvider><DashboardContent data={data} /><DomIds /></SidebarProvider>;
}

/** Loads one task in place: cached copy at once, fresh copy from /api/tasks/{id}. */
function useTaskDetail(id: string | undefined, version: string | undefined, live: boolean) {
  const [, setTick] = useState(0);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    const load = () => fetch(`/api/tasks/${encodeURIComponent(id)}`, { cache: "no-store", signal: controller.signal })
      .then(async response => {
        if (response.status === 404) throw new Error(`Task ${id} does not exist.`);
        if (!response.ok) throw new Error("The task could not be loaded. Try again shortly.");
        detailCache.set(id, await response.json() as TaskDetail);
        setError(null); setTick(tick => tick + 1);
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError({ id, message: reason.message }); });
    void load();
    const timer = live ? setInterval(load, 5000) : undefined;
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [id, version, live, nonce]);
  return { detail: id ? detailCache.get(id) : undefined, error: error && error.id === id ? error.message : null, reload: useCallback(() => setNonce(n => n + 1), []) };
}

function DashboardContent({ data }: { data: DashboardData }) {
  const { setOpenMobile } = useSidebar();
  const [route, setRoute] = useState<Route>({ view: "overview" });
  const page: View = route.view;
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const { scout } = data;
  const ceo = scout.ceoId;
  const names = useMemo(() => Object.fromEntries(data.employees.map(person => [person.id, person.name])), [data.employees]);
  const openTasks = scout.tasks.filter(isOpen);
  const inboxTasks = openTasks.filter(task => inInbox(task, ceo));
  const waiting = scout.tasks.filter(task => task.status === "waiting").length;
  const escalated = scout.tasks.filter(task => task.status === "escalated").length;
  const attention = waiting + escalated;
  const { seen, markSeen } = useSeenTasks(scout.tasks);
  const counts: NavCounts = {
    inbox: inboxTasks.length, openTasks: openTasks.length,
    loops: Object.fromEntries(LOOPS.map(loop => [loop, openTasks.filter(task => task.loop === loop).length])),
    signals: Object.fromEntries(LOOPS.map(loop => [loop, loopSignal(scout.tasks, loop, seen)])),
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

  // Views switch on the client only: the data is already here, so there is no request and no wait.
  const navigate = (next: Route, { replace = false }: { replace?: boolean } = {}) => {
    searchNavigated.current = searchOpen;
    setOpenMobile(false);
    setSearchOpen(false);
    setRoute(next);
    const hash = hashFromRoute(next);
    if (window.location.hash !== hash) {
      if (replace) window.history.replaceState(window.history.state, "", hash);
      else window.history.pushState(window.history.state, "", hash);
    }
    if (!replace) requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
  };
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const openTask = useCallback((task: AgentTask) => navigateRef.current({ view: "tasks", taskId: task.id }), []);

  useEffect(() => {
    const fromHash = () => setRoute(routeFromHash(window.location.hash));
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
    window.addEventListener("popstate", fromHash);
    window.addEventListener("keydown", shortcut);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      window.removeEventListener("popstate", fromHash);
      window.removeEventListener("keydown", shortcut);
    };
  }, []);

  useEffect(() => { mainRef.current?.focus({ preventScroll: true }); window.scrollTo({ top: 0 }); }, [page, route.taskId]);

  // The open task, in place.
  const listTask = route.taskId ? scout.tasks.find(task => task.id === route.taskId) : undefined;
  const task = useTaskDetail(route.taskId, listTask?.updatedAt, !!listTask && LIVE(listTask));
  useEffect(() => { const opened = task.detail?.task ?? listTask; if (opened) markSeen(opened); }, [task.detail, listTask, markSeen]);

  // Tasks visible in the current view; poll while any of them is still moving.
  const filter: TaskFilter = route.filter ?? {};
  const visibleTasks = route.taskId ? [] : page === "tasks" ? scout.tasks.filter(item => !filter.loop || item.loop === filter.loop) : page === "overview" ? openTasks : [];
  const live = visibleTasks.some(LIVE);
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => { if (document.visibilityState === "visible") router.refresh(); }, 5000);
    return () => clearInterval(timer);
  }, [live, router]);

  const views: { label: string; detail: string; route: Route }[] = [
    { label: "Tasks", detail: `${openTasks.length} open tasks`, route: { view: "tasks" } },
    { label: "Inbox", detail: `${inboxTasks.length} waiting on you`, route: { view: "tasks", filter: { scope: "inbox" } } },
    { label: "Scout chat", detail: "Ask Scout; it answers with its tools", route: { view: "chat" } },
    { label: "LLM usage", detail: "Model requests, tokens, latency and cost", route: { view: "llm-usage" } },
    { label: "Overview", detail: "Cash balance, forecast and expense mix", route: { view: "overview" } },
    { label: "Transactions", detail: "Bank bookings and their invoices", route: { view: "transactions" } },
    { label: "Connections", detail: "Bank, invoicing, email, Slack and laptops", route: { view: "connections" } },
    { label: "Audit log", detail: "ALLOW / DENY decisions", route: { view: "audit" } },
    { label: "Staffing & payroll", detail: "Team, salaries and payroll export", route: { view: "staffing" } },
  ];
  const searchItems = [
    ...views,
    ...scout.tasks.map(item => ({ label: `${item.id} · ${item.title}`, detail: `${item.status.replace("_", " ")} · ${LOOP_LABEL[item.loop] ?? item.loop}${item.counterparty ? ` · ${item.counterparty}` : ""}`, route: { view: "tasks", taskId: item.id } as Route })),
    ...data.employees.map((person) => ({ label: person.name, detail: `${person.role} · ${person.team}`, route: { view: "staffing" } as Route })),
  ].filter((item) => `${item.label} ${item.detail}`.toLowerCase().includes(search.toLowerCase().trim())).slice(0, 40);

  const active = activeItem(route);
  const title = route.taskId ? `Tasks › ${route.taskId}` : route.view === "tasks" ? active?.label ?? "Tasks" : VIEW_LABEL[page];
  const ceoName = names[ceo] ?? "CEO";
  const attentionTasks = [...openTasks].sort((a, b) => Number(needsReaction(b)) - Number(needsReaction(a)) || b.updatedAt.localeCompare(a.updatedAt));

  let content: React.ReactNode;
  if (route.taskId) content = task.detail ? <TaskDetailView key={route.taskId} detail={task.detail} names={names} onChanged={task.reload} />
    : task.error ? <div role="alert" className="rounded-xl border bg-card px-4 py-10 text-center text-sm text-muted-foreground">{task.error} <Link href="/#tasks" className="underline underline-offset-2">Back to tasks</Link></div>
    : <TaskDetailSkeleton id={route.taskId} />;
  else if (page === "tasks") content = <div className="space-y-4">
    {scout.demo && <DemoPanel />}
    <TaskBoard tasks={scout.tasks} names={names} ceo={ceo} filter={filter} seen={seen} onOpen={openTask}
      onFilter={next => navigate({ view: "tasks", filter: next }, { replace: true })} />
  </div>;
  else if (page === "staffing") content = <Staffing data={data} onBack={() => navigate({ view: "overview" })} />;
  else if (page === "transactions") content = <Transactions rows={scout.transactions} tasks={scout.tasks} />;
  else if (page === "connections") content = <Connections connections={scout.connections} />;
  else if (page === "audit") content = <AuditLog rows={scout.audit} />;
  else if (page === "policies") content = <AccessPolicies />;
  else if (page === "llm-usage") content = <LlmUsage active />;
  else if (page === "chat") content = null; // Kept mounted below so the conversation survives page switches.
  else content = <div id="scout-overview" className="space-y-6">
    <section className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
      <div className="space-y-2"><p className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">Scout · Your autonomous CFO</p><h1 className="text-3xl font-medium tracking-tight">Your finance overview</h1><p className="max-w-2xl text-sm leading-6 text-muted-foreground">{data.year.transactions.toLocaleString("en-US")} bank transactions through {data.asOf}. {attention} tasks wait on people. Every outcome connected to its evidence.</p></div>
      <Button id="scout-refresh" className="min-h-11 shrink-0 gap-2" disabled={refreshing} onClick={() => startRefresh(() => router.refresh())}><Activity className={cn("size-4", refreshing && "motion-safe:animate-spin")} aria-hidden="true" />{refreshing ? "Refreshing…" : "Refresh records"}</Button>
    </section>

    {scout.demo && <DemoPanel />}

    <section id="scout-financial-summary" aria-label="Financial summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      {[
        { label: "Available balance", value: currency(data.closingBalance), note: `As of ${data.asOf}`, trend: "Sum of recorded bank bookings", icon: Wallet, tone: "positive", to: { view: "transactions" } as Route },
        { label: "Money in", value: compact(data.year.totalRevenue), note: `Recorded inflows · ${data.yearLabel}`, trend: "Company bank transactions", icon: ArrowDownLeft, tone: "neutral", to: { view: "transactions" } as Route },
        { label: "Money out", value: compact(data.year.totalExpenses), note: `Recorded outflows · ${data.yearLabel}`, trend: `${data.year.expenses.length} categories tracked`, icon: ArrowUpRight, tone: "neutral", to: { view: "transactions" } as Route },
        { label: "Needs attention", value: String(attention), note: "Waiting + escalated tasks", trend: `${escalated} escalated`, icon: Activity, tone: "warning", to: { view: "tasks", filter: { scope: "inbox" } } as Route },
        { label: "Explained by Scout", value: `${scout.explained.documented}/${scout.explained.total}`, note: `${formatShare(scout.explained.documented, scout.explained.total)} of payments`, trend: "Outgoing payments with an invoice reference", icon: ShieldCheck, tone: "positive", to: { view: "transactions" } as Route },
      ].map(({ label, value, note, trend, icon: Icon, tone, to }) => (
        <Card key={label} className="cursor-pointer gap-3 py-5 shadow-none transition-colors hover:border-foreground/20" onClick={() => navigate(to)}>
          <CardContent className="space-y-3 px-4">
            <div className="flex items-center justify-between gap-3"><span className={`flex size-8 items-center justify-center rounded-md border ${tone === "positive" ? "border-success-border text-success" : tone === "warning" ? "border-warning-border text-warning" : "text-muted-foreground"}`}><Icon className="size-4" aria-hidden="true" /></span><p className="text-xs text-muted-foreground">{label}</p></div>
            <div><strong className="block font-mono text-[1.65rem] font-medium tracking-tighter">{value}</strong><span className="mt-1 block text-[11px] text-muted-foreground">{note}</span></div>
            <p className={`pt-1 text-[11px] ${tone === "positive" ? "text-success" : tone === "warning" ? "text-warning" : "text-muted-foreground"}`}>{trend}</p>
          </CardContent>
        </Card>
      ))}
    </section>

    <FinancialCharts data={data} />

    <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]" aria-label="Cash events and open tasks">
      <CashEvents data={data} />
      <AttentionList tasks={attentionTasks} names={names} onOpen={openTask} onShowAll={() => navigate({ view: "tasks" })} />
    </section>

    <Card id="scout-priority-inbox" aria-labelledby="priority-title" className="gap-5">
      <CardHeader className="flex flex-row justify-between gap-4"><div className="space-y-1.5"><CardTitle id="priority-title" className="text-base font-medium">Priority inbox</CardTitle><CardDescription>What Scout could not settle on its own</CardDescription></div><Badge variant="outline" className="h-fit shrink-0 border-warning-border text-warning">{attention} open</Badge></CardHeader>
      <CardContent className="divide-y">
        <PriorityItem label="Escalated" title={`${escalated} escalated tasks`} description="Nobody answered Scout's questions in time; the task moved up to the CEO." action="Open inbox" tone="red" onClick={() => navigate({ view: "tasks", filter: { scope: "inbox" } })} />
        <PriorityItem label="Waiting" title={`${waiting} tasks waiting on people`} description="Scout asked someone on Slack or is waiting for an approval." action="View tasks" tone="orange" onClick={() => navigate({ view: "tasks", filter: { status: "waiting" } })} />
        <PriorityItem label="Documents" title={`${data.undocumentedPayments} payments without invoice documents`} description="Bank bookings with an invoice reference that are not documented yet." action="Transactions" tone="neutral" onClick={() => navigate({ view: "transactions" })} />
      </CardContent>
    </Card>

    <Card id="scout-recorded-payroll" className="gap-4"><CardHeader><CardTitle className="text-base font-medium">Latest recorded payroll</CardTitle><CardDescription>Latest month with salary bookings</CardDescription></CardHeader><CardContent className="space-y-4"><div className="flex items-center justify-between gap-3"><div><span className="text-xs text-muted-foreground">{data.employees.filter(employee => employee.salary !== null).length} recorded recipients</span><strong className="mt-1 block font-mono text-2xl font-medium tracking-tight">{currency(data.monthlyPayroll)}</strong></div><div className="flex -space-x-2" aria-label="Team members">{data.employees.map(employee=><Avatar key={employee.id} className="size-8 border-2 border-card"><AvatarFallback className="bg-secondary text-[10px]">{employee.initials}</AvatarFallback></Avatar>)}</div></div><Button variant="secondary" size="sm" className="min-h-10" onClick={() => navigate({ view: "staffing" })}>Review staffing</Button></CardContent></Card>
  </div>;

  const chat = page === "chat" && !route.taskId;
  return (
    <>
      <a id="scout-skip-link" href="#main-content" onClick={event=>{event.preventDefault();mainRef.current?.focus();}} className="fixed top-3 left-3 z-50 -translate-y-24 rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground focus:translate-y-0">Skip to content</a>
      <AppSidebar active={route.taskId ? "tasks" : active?.id ?? null} counts={counts} user={{ name: ceoName, role: "CEO · dashboard login" }} onNavigate={route => navigate(route)} onSearch={openSearch}
        onNewTask={() => setNotice({ title: "New task", description: "Scout opens tasks from its loops. To ask for something new, DM Scout in Slack, e.g. “what if I buy an oven for 30k in March?” — it appears here under Tasks." })} />

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
          <ThemeToggle className="size-8 border-0 shadow-none" />
          <Button id="scout-notifications" variant="ghost" size="icon" aria-label={`Inbox, ${inboxTasks.length} waiting`} className="relative size-8 shrink-0" onClick={() => navigate({ view: "tasks", filter: { scope: "inbox" } })}><Bell className="size-4" aria-hidden="true" />{inboxTasks.length > 0 && <span className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-amber-500" aria-hidden="true" />}</Button>
        </header>

        <section id="main-content" ref={mainRef} tabIndex={-1} aria-label={title}
          className={cn("mx-auto w-full max-w-[1600px] min-w-0 px-4 outline-none md:px-8",
            chat ? "flex h-[calc(100dvh-3rem-5.5rem)] flex-col pt-4 pb-4 md:h-[calc(100dvh-3rem)] md:pt-5 md:pb-5" : "pt-6 pb-28 md:pt-7 md:pb-10")}>
          {content}
          <div hidden={!chat} className={cn(chat && "flex min-h-0 flex-1 flex-col")}><LlmChat active={chat} /></div>
        </section>

        <nav id="scout-mobile-navigation" aria-label="Mobile navigation" className="fixed right-0 bottom-0 left-0 z-30 grid grid-cols-4 gap-2 border-t bg-sidebar/95 px-3 pt-2 pb-[max(.5rem,env(safe-area-inset-bottom))] backdrop-blur-sm md:hidden">
          {mobileNavigation.map(({route: target, id, label, icon: Icon})=>{ const current = (active?.id ?? null) === id && !route.taskId;
            return <Button key={id} id={`scout-mobile-${id}`} variant={current?"secondary":"ghost"} aria-current={current?"page":undefined} onClick={()=>navigate(target)} className="h-auto min-h-14 flex-col gap-1 text-[11px]"><Icon className="size-4" aria-hidden="true" />{label}</Button>; })}
        </nav>
      </SidebarInset>

      <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
        <DialogContent id="scout-search-dialog" onCloseAutoFocus={event=>{event.preventDefault();if(searchNavigated.current){mainRef.current?.focus();searchNavigated.current=false;}else{searchOrigin.current?.focus();}}} className="max-w-xl"><DialogHeader><DialogTitle>Search your workspace</DialogTitle><DialogDescription>Find views, agent tasks and people.</DialogDescription></DialogHeader><Input id="scout-search-input" aria-label="Search workspace" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Search tasks, people…" /><div className="max-h-80 space-y-1 overflow-y-auto" aria-live="polite">{searchItems.length ? searchItems.map(item=><Button key={item.label} variant="ghost" className="h-auto min-h-14 w-full justify-start px-3 py-2.5 text-left" onClick={()=>navigate(item.route)}><span><span className="block text-sm">{item.label}</span><span className="mt-1 block whitespace-normal text-xs font-normal text-muted-foreground">{item.detail}</span></span></Button>):<p className="p-3 text-sm text-muted-foreground">No matching records.</p>}</div></DialogContent>
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
        <span className="min-w-0 flex-1 truncate">{event.label}{event.taskId && <> · <Link href={`/#task/${encodeURIComponent(event.taskId)}`} className="font-mono text-xs text-muted-foreground hover:underline">{event.taskId}</Link></>}</span>
        <span className={`shrink-0 font-mono text-xs ${event.amount > 0 ? "text-success" : ""}`}>{event.amount > 0 ? "+" : ""}{money(event.amount)}</span>
      </li>)}</ul>}
  </section>;
}

function PriorityItem({ label, title, description, action, tone, onClick }: {label:string;title:string;description:string;action:string;tone:"red"|"orange"|"neutral";onClick:()=>void}) {
  return <article className="flex flex-col justify-between gap-4 py-5 first:pt-0 last:pb-0 sm:flex-row sm:items-center"><div className="space-y-2"><Badge variant="outline" className={`font-normal ${tone==="red"?"border-danger-border text-danger":tone==="orange"?"border-warning-border text-warning":"text-muted-foreground"}`}>{label}</Badge><h3 className="text-sm font-medium">{title}</h3><p className="max-w-3xl text-xs leading-6 text-muted-foreground">{description}</p></div><Button variant="outline" size="sm" className="min-h-10 w-fit shrink-0" onClick={onClick}>{action}</Button></article>;
}
