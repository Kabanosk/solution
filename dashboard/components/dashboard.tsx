"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Bell, CalendarClock, ChevronRight, LayoutGrid, ListTodo, MessageSquare, Plug, RefreshCw, Search, type LucideIcon } from "lucide-react";
import Link from "next/link";
import type { DashboardData } from "@/lib/finance";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { SidebarInset, SidebarProvider, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { ForecastSection } from "@/components/overview/forecast-section";
import { ScoutActivityRing, TeamSpendDonut } from "@/components/overview/round-charts";
import { Staffing } from "@/components/staffing";
import { DomIds } from "@/components/dom-ids";
import { AppSidebar, type NavCounts } from "@/components/app-sidebar";
import { AttentionList, TaskBoard } from "@/components/tasks/task-list";
import { TaskDetailSkeleton, TaskDetailView } from "@/components/tasks/task-detail";
import { AuditLog } from "@/components/audit-log";
import { Connections } from "@/components/connections";
import { Onboarding } from "@/components/onboarding";
import { canEdit, fetchSetup, shouldOpenSetup } from "@/lib/onboarding-client";
import { AccessPolicies } from "@/components/access-policies";
import { DemoView } from "@/components/demo-panel";
import { Transactions } from "@/components/transactions";
import { LlmChat } from "@/components/llm-chat";
import { LlmUsage } from "@/components/llm-usage";
import { ThemeToggle } from "@/components/theme-toggle";
import { activeItem, GO_SHORTCUTS, hashFromRoute, routeFromHash, VIEW_LABEL, type Route, type TaskFilter, type View } from "@/lib/nav";
import { formatShare, isOpen, LOOP_LABEL, money, shortDate, type AgentTask, type TaskDetail } from "@/lib/scout-model";
import { inInbox, loopSignal, needsReaction } from "@/lib/task-signals";
import { latestWhatIf } from "@/lib/overview-model";
import { useSeenTasks } from "@/lib/use-seen-tasks";
import { prefetchRecords, useRecords } from "@/lib/use-records";
import { ViewSkeleton } from "@/components/view-skeletons";
import { prefetchLlmUsage } from "@/components/llm-usage";
import { cn } from "@/lib/utils";
import { tenantFetch, scopeKey } from "@/lib/tenant-browser";

const currency = (amount: number) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format(amount);
const mobileNavigation: { route: Route; id: string; label: string; icon: LucideIcon }[] = [
  { route: { view: "overview" }, id: "overview", label: "Overview", icon: LayoutGrid },
  { route: { view: "chat" }, id: "chat", label: "Scout chat", icon: MessageSquare },
  { route: { view: "tasks" }, id: "tasks", label: "Tasks", icon: ListTodo },
];
const LIVE = (task: AgentTask) => task.status === "in_progress" || task.status === "waiting";
const LOOPS = ["invoicing", "cost_control", "liquidity", "chat"];

// Task details opened in this tab, so going back to a task is instant (refreshed in the background).
const detailCache = new Map<string, TaskDetail>();

export function Dashboard({ data }: { data: DashboardData }) {
  // Assign IDs only after this client tree hydrates, rather than from the
  // layout while the streamed dashboard is still waiting to hydrate.
  return <div id="scout-tenant-boundary" data-scout-scope={`${data.workspace?.slug}:${data.workspace?.userId}`}><SidebarProvider><DashboardContent data={data} /><DomIds /></SidebarProvider></div>;
}

/** Loads one task in place: cached copy at once, fresh copy from /api/tasks/{id}. */
function useTaskDetail(id: string | undefined, version: string | undefined, live: boolean) {
  const [, setTick] = useState(0);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    const load = () => tenantFetch(`/api/tasks/${encodeURIComponent(id)}`, { cache: "no-store", signal: controller.signal })
      .then(async response => {
        if (response.status === 404) throw new Error(`Task ${id} does not exist.`);
        if (!response.ok) throw new Error("The task could not be loaded. Try again shortly.");
        detailCache.set(scopeKey(id), await response.json() as TaskDetail);
        setError(null); setTick(tick => tick + 1);
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError({ id, message: reason.message }); });
    void load();
    const timer = live ? setInterval(load, 5000) : undefined;
    return () => { controller.abort(); if (timer) clearInterval(timer); };
  }, [id, version, live, nonce]);
  return { detail: id ? detailCache.get(scopeKey(id)) : undefined, error: error && error.id === id ? error.message : null, reload: useCallback(() => setNonce(n => n + 1), []) };
}

function DashboardContent({ data }: { data: DashboardData }) {
  const { setOpenMobile } = useSidebar();
  const [route, setRoute] = useState<Route>({ view: "chat" });
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

  // A new owner's pending setup opens on an ordinary load. An explicit hash (deep link, #setup, #chat) is never overridden,
  // and a failed lookup leaves the default view alone.
  const role = data.workspace?.role;
  useEffect(() => {
    if (window.location.hash || !canEdit(role)) return;
    const controller = new AbortController();
    fetchSetup(tenantFetch, controller.signal).then(setup => {
      if (shouldOpenSetup({ hash: window.location.hash, role, status: setup.status })) navigateRef.current({ view: "setup" }, { replace: true });
    }, () => undefined);
    return () => controller.abort();
  }, [role]);

  // Once the first view is on screen, warm the data of the views that load separately, so switching to them is instant.
  useEffect(() => {
    const idle = window.requestIdleCallback ?? ((callback: () => void) => window.setTimeout(callback, 1200));
    const handle = idle(() => { prefetchRecords("transactions"); prefetchRecords("audit"); prefetchLlmUsage(7); });
    return () => { (window.cancelIdleCallback ?? window.clearTimeout)(handle as number); };
  }, []);

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
    { label: "Overview", detail: "Cash, forecast, safety buffer, team spend and what Scout did", route: { view: "overview" } },
    { label: "Transactions", detail: "Bank bookings and their invoices", route: { view: "transactions" } },
    { label: "Connections", detail: "Bank, invoicing, email, Slack and laptops", route: { view: "connections" } },
    { label: "Audit log", detail: "ALLOW / DENY decisions", route: { view: "audit" } },
    { label: "Staffing & payroll", detail: "Team, salaries and payroll export", route: { view: "staffing" } },
    ...(scout.demo ? [{ label: "Demo scenarios", detail: "Play a payment scenario for Scout", route: { view: "demo" } as Route }] : []),
  ];
  const searchItems = [
    ...views,
    ...scout.tasks.map(item => ({ label: `${item.id} · ${item.title}`, detail: `${item.status.replace("_", " ")} · ${LOOP_LABEL[item.loop] ?? item.loop}${item.counterparty ? ` · ${item.counterparty}` : ""}`, route: { view: "tasks", taskId: item.id } as Route })),
    ...data.employees.map((person) => ({ label: person.name, detail: `${person.role} · ${person.team}`, route: { view: "staffing" } as Route })),
  ].filter((item) => `${item.label} ${item.detail}`.toLowerCase().includes(search.toLowerCase().trim())).slice(0, 40);

  const active = activeItem(route);
  const title = route.taskId ? `Tasks › ${route.taskId}` : route.view === "tasks" ? active?.label ?? "Tasks" : VIEW_LABEL[page];
  const ceoName = names[ceo] ?? "CEO";
  const whatIf = useMemo(() => latestWhatIf(scout.tasks), [scout.tasks]);
  const attentionTasks = [...openTasks].sort((a, b) => Number(needsReaction(b)) - Number(needsReaction(a)) || b.updatedAt.localeCompare(a.updatedAt));

  let content: React.ReactNode;
  if (route.taskId) content = task.detail ? <TaskDetailView key={route.taskId} detail={task.detail} names={names} onChanged={task.reload} />
    : task.error ? <div role="alert" className="rounded-xl border bg-card px-4 py-10 text-center text-sm text-muted-foreground">{task.error} <Link href="#tasks" className="underline underline-offset-2">Back to tasks</Link></div>
    : <TaskDetailSkeleton id={route.taskId} />;
  else if (page === "tasks") content = <TaskBoard tasks={scout.tasks} names={names} ceo={ceo} filter={filter} seen={seen} onOpen={openTask}
      onFilter={next => navigate({ view: "tasks", filter: next }, { replace: true })} />;
  else if (page === "demo") content = scout.demo ? <DemoView />
    : <p role="status" className="rounded-xl border bg-card px-4 py-10 text-center text-sm text-muted-foreground">Demo scenarios are switched off on this deployment (SCOUT_DEMO is not set).</p>;
  else if (page === "staffing") content = <Staffing data={data} onBack={() => navigate({ view: "overview" })} />;
  else if (page === "transactions") content = <TransactionsView tasks={scout.tasks} />;
  else if (page === "connections") content = <Connections connections={scout.connections} onOpenSetup={canEdit(role) ? () => navigate({ view: "setup" }) : undefined} />;
  else if (page === "setup") content = <Onboarding role={data.workspace?.role} onContinue={() => navigate({ view: "chat" })} />;
  else if (page === "audit") content = <AuditView />;
  else if (page === "policies") content = <AccessPolicies />;
  else if (page === "llm-usage") content = <LlmUsage active />;
  else if (page === "chat") content = null; // Kept mounted below so the conversation survives page switches.
  else if (data.year.transactions === 0) content = <div id="scout-empty-overview" className="space-y-6">
    <section id="scout-empty-organization" aria-labelledby="scout-empty-title" className="flex min-h-[55dvh] flex-col items-center justify-center px-2 py-12 text-center">
      <div className="mb-5 flex size-12 items-center justify-center rounded-xl border bg-muted/30"><LayoutGrid className="size-5 text-muted-foreground" strokeWidth={1.5} aria-hidden="true" /></div>
      <h1 id="scout-empty-title" className="text-xl font-medium tracking-tight">Your workspace is ready</h1>
      <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">No transactions yet. Your financial overview will appear when data is added to this organization.</p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Button id="scout-empty-chat" className="h-11 gap-2" onClick={() => navigate({ view: "chat" })}><MessageSquare className="size-4" aria-hidden="true" />Ask Scout</Button>
        <Button id="scout-empty-connections" variant="outline" className="h-11 gap-2" onClick={() => navigate({ view: "connections" })}><Plug className="size-4 text-muted-foreground" aria-hidden="true" />View connections</Button>
      </div>
      <p className="mt-5 max-w-sm text-xs leading-5 text-muted-foreground">A clean start. No sample records or shared connections.</p>
    </section>
    {attentionTasks.length > 0 && <AttentionList tasks={attentionTasks} names={names} onOpen={openTask} onShowAll={() => navigate({ view: "tasks" })} limit={5} />}
  </div>;
  else content = <div id="scout-overview" className="space-y-4">
    <section className="flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-xl font-medium tracking-tight">Overview</h1><p className="mt-1 text-[13px] text-muted-foreground">{data.year.transactions.toLocaleString("en-US")} bank transactions through {shortDate(`${data.asOf}T12:00:00Z`)} · {attention} tasks wait on people</p></div>
      <Button id="scout-refresh" variant="outline" size="sm" className="h-8 shrink-0 gap-2" disabled={refreshing} aria-busy={refreshing} onClick={() => startRefresh(() => router.refresh())}><RefreshCw className={cn("size-3.5", refreshing && "motion-safe:animate-spin")} aria-hidden="true" />Refresh</Button>
    </section>

    <section id="scout-financial-summary" aria-label="Key figures" className="grid divide-y overflow-hidden rounded-xl border bg-card sm:grid-cols-3 sm:divide-x sm:divide-y-0">
      {[
        { label: "Available balance", value: currency(data.closingBalance), note: `Recorded bank bookings · ${shortDate(`${data.asOf}T12:00:00Z`)}`, to: { view: "transactions" } as Route, tone: "" },
        { label: "Needs attention", value: String(attention), note: `${waiting} waiting · ${escalated} escalated`, to: { view: "tasks", filter: { scope: "inbox" } } as Route, tone: "" },
        { label: "Explained by Scout", value: formatShare(scout.explained.documented, scout.explained.total), note: `${scout.explained.documented} of ${scout.explained.total} payments with an invoice reference`, to: { view: "transactions" } as Route, tone: "" },
      ].map(({ label, value, note, to, tone }) => (
        <button key={label} type="button" onClick={() => navigate(to)} className="group px-4 py-3 text-left transition-colors hover:bg-muted/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring">
          <span className="flex items-center justify-between text-[12px] text-muted-foreground">{label}<ChevronRight className="size-3.5 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden="true" /></span>
          <strong className={cn("mt-1 block font-mono text-[22px] leading-tight font-medium tracking-tight", tone)}>{value}</strong>
          <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">{note}</span>
        </button>
      ))}
    </section>

    <ForecastSection quarter={data.forecast} year={data.forecastYear} whatIf={whatIf} />

    <section aria-label="Spend and Scout activity" className="grid gap-4 lg:grid-cols-2">
      <TeamSpendDonut spend={data.quarterSpend} />
      <ScoutActivityRing tasks={scout.tasks} now={data.generatedAt} />
    </section>

    {/* Upcoming cash events only when there are any: the overview shows as little as it can. */}
    <section className={cn("grid gap-4", data.scout.cashEvents.length > 0 && "lg:grid-cols-2")} aria-label={data.scout.cashEvents.length > 0 ? "Cash events and open tasks" : "Open tasks"}>
      {data.scout.cashEvents.length > 0 && <CashEvents data={data} />}
      <AttentionList tasks={attentionTasks} names={names} onOpen={openTask} onShowAll={() => navigate({ view: "tasks" })} limit={5} />
    </section>
  </div>;

  const chat = page === "chat" && !route.taskId;
  return (
    <>
      <a id="scout-skip-link" href="#main-content" onClick={event=>{event.preventDefault();mainRef.current?.focus();}} className="fixed top-3 left-3 z-50 -translate-y-24 rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground focus:translate-y-0">Skip to content</a>
      <AppSidebar active={route.taskId ? "tasks" : active?.id ?? null} counts={counts} googleAvailable={data.workspace?.googleAvailable ?? false} organizationSlug={data.workspace?.slug ?? ""} organizationName={data.workspace?.name ?? "Organization"} user={{ name: data.workspace?.userName ?? ceoName, role: data.workspace?.role ?? "Member" }} demo={scout.demo} onNavigate={route => navigate(route)} onSearch={openSearch}
        onNewTask={() => setNotice({ title: "New task", description: "Scout opens tasks from its loops. To ask for something new, DM Scout in Slack, e.g. “what if I buy an oven for 30k in March?” — it appears here under Tasks." })} />

      <SidebarInset id="scout-workspace" className="min-w-0 bg-background">
        <header id="scout-topbar" className="sticky top-0 z-20 flex h-12 shrink-0 items-center gap-3 border-b bg-background/95 px-4 backdrop-blur-sm md:px-6">
          <SidebarTrigger aria-label="Toggle navigation" />
          <div className="flex min-w-0 flex-1 items-center gap-2 text-[13px]">
            <span className="hidden text-muted-foreground lg:inline">{data.workspace?.name ?? "Organization"}</span><span className="hidden text-muted-foreground lg:inline" aria-hidden="true">/</span>
            <strong className="truncate font-medium">{title}</strong>
          </div>
          <Button id="scout-search-trigger" variant="outline" onClick={openSearch} className="hidden h-8 w-full max-w-xs justify-start gap-2 bg-card text-muted-foreground lg:flex">
            <Search className="size-4" aria-hidden="true" /><span className="flex-1 text-left text-xs font-normal">Search tasks, people…</span><kbd className="rounded border px-1.5 py-0.5 font-mono text-[10px]">⌘ K</kbd>
          </Button>
          <Button id="scout-search-compact" variant="ghost" size="icon" className="size-8 shrink-0 lg:hidden" aria-label="Search workspace" onClick={openSearch}><Search className="size-4" aria-hidden="true" /></Button>
          <ThemeToggle className="size-8 border-0 shadow-none" />
          <Button id="scout-notifications" variant="ghost" size="icon" aria-label={`Inbox, ${inboxTasks.length} waiting`} className="relative size-8 shrink-0" onClick={() => navigate({ view: "tasks", filter: { scope: "inbox" } })}><Bell className="size-4" aria-hidden="true" />{inboxTasks.length > 0 && <span className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-warning-mark" aria-hidden="true" />}</Button>
        </header>

        <section id="main-content" ref={mainRef} tabIndex={-1} aria-label={title}
          className={cn("mx-auto w-full max-w-[1600px] min-w-0 px-4 outline-none md:px-8",
            chat ? "flex h-[calc(100dvh-3rem-5.5rem)] flex-col pt-4 pb-4 md:h-[calc(100dvh-3rem)] md:pt-5 md:pb-5" : "pt-6 pb-28 md:pt-7 md:pb-10")}>
          {data.workspace?.authNotice && <p id="scout-auth-feedback" role="status" className="mb-5 rounded-md border bg-muted/40 px-4 py-3 text-sm">{data.workspace.authNotice}</p>}
          {content}
          <div hidden={!chat} className={cn(chat && "flex min-h-0 flex-1 flex-col")}><LlmChat active={chat} /></div>
        </section>

        <nav id="scout-mobile-navigation" aria-label="Mobile navigation" className="fixed right-0 bottom-0 left-0 z-30 grid grid-cols-3 gap-2 border-t bg-sidebar/95 px-3 pt-2 pb-[max(.5rem,env(safe-area-inset-bottom))] backdrop-blur-sm md:hidden">
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

function RecordsError({ onRetry }: { onRetry: () => void }) {
  return <div role="alert" className="rounded-xl border bg-card px-4 py-10 text-center text-sm text-muted-foreground">These records could not be loaded. <Button variant="link" className="h-auto p-0" onClick={onRetry}>Try again</Button></div>;
}

function TransactionsView({ tasks }: { tasks: AgentTask[] }) {
  const { data, error, reload } = useRecords("transactions");
  return data ? <Transactions rows={data} tasks={tasks} /> : error ? <RecordsError onRetry={reload} /> : <ViewSkeleton view="transactions" />;
}

function AuditView() {
  const { data, error, reload } = useRecords("audit");
  return data ? <AuditLog rows={data} /> : error ? <RecordsError onRetry={reload} /> : <ViewSkeleton view="audit" />;
}

function CashEvents({ data }: { data: DashboardData }) {
  const events = data.scout.cashEvents;
  return <section aria-labelledby="cash-events-title" className="h-fit overflow-hidden rounded-xl border bg-card">
    <h2 id="cash-events-title" className="flex items-center gap-2 border-b px-4 py-2.5 text-[13px] font-medium"><CalendarClock className="size-4 text-muted-foreground" aria-hidden="true" />Upcoming cash events</h2>
    {events.length === 0 ? <p className="px-4 py-6 text-sm text-muted-foreground">No expected cash events.</p> :
      <ul>{events.map(event => <li key={event.id} className="flex items-center gap-4 border-b px-4 py-2.5 text-[13px] last:border-b-0">
        <span className="w-14 shrink-0 font-mono text-xs text-muted-foreground">{shortDate(`${event.onDate}T12:00:00Z`)}</span>
        <span className="min-w-0 flex-1 truncate">{event.label}{event.taskId && <> · <Link href={`#task/${encodeURIComponent(event.taskId)}`} className="font-mono text-xs text-muted-foreground hover:underline">{event.taskId}</Link></>}</span>
        <span className={`shrink-0 font-mono text-xs ${event.amount > 0 ? "text-success" : ""}`}>{event.amount > 0 ? "+" : ""}{money(event.amount)}</span>
      </li>)}</ul>}
  </section>;
}
