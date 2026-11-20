"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { Activity, ArrowDownLeft, ArrowUpRight, Bell, ChevronDown, Cpu, GitBranch, LayoutDashboard, Search, Sparkles, Users, Wallet } from "lucide-react";
import type { DashboardData } from "@/lib/finance";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger, useSidebar } from "@/components/ui/sidebar";
import { FinancialCharts } from "@/components/financial-charts";
import { AgentJobs } from "@/components/agent-jobs";
import { Staffing } from "@/components/staffing";
import { ThemeToggle } from "@/components/theme-toggle";
import { DomIds } from "@/components/dom-ids";
import { LlmChat } from "@/components/llm-chat";

type Page = "overview" | "staffing" | "agent-jobs" | "llm";
const navigation = [
  { page: "overview" as const, label: "Overview", icon: LayoutDashboard },
  { page: "staffing" as const, label: "Staffing", icon: Users },
  { page: "agent-jobs" as const, label: "Agent jobs", icon: GitBranch },
  { page: "llm" as const, label: "LLM", icon: Cpu },
];
const currency = (amount: number) => new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" }).format(amount);
const compact = (amount: number) => amount >= 1000 ? `€${(amount / 1000).toFixed(1)}k` : currency(amount);

export function Dashboard({ data }: { data: DashboardData }) {
  // Assign IDs only after this client tree hydrates, rather than from the
  // layout while the streamed dashboard is still waiting to hydrate.
  return <SidebarProvider><DashboardContent data={data} /><DomIds /></SidebarProvider>;
}

function DashboardContent({ data }: { data: DashboardData }) {
  const { setOpenMobile } = useSidebar();
  const [page, setPage] = useState<Page>("overview");
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const attention = data.undocumentedPayments + data.pendingApprovals;
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [notice, setNoticeState] = useState<{ title: string; description: string } | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const searchOrigin = useRef<HTMLElement | null>(null);
  const noticeOrigin = useRef<HTMLElement | null>(null);
  const searchNavigated = useRef(false);
  const openSearch = () => { searchOrigin.current = document.activeElement as HTMLElement; searchNavigated.current = false; setSearchOpen(true); };
  const setNotice = (value: { title: string; description: string } | null) => { if(value) noticeOrigin.current = document.activeElement as HTMLElement; setNoticeState(value); };

  const navigate = (next: Page) => {
    searchNavigated.current = searchOpen;
    setOpenMobile(false);
    setPage(next);
    requestAnimationFrame(() => mainRef.current?.focus({ preventScroll: true }));
    if (window.location.hash !== `#${next}`) window.location.hash = next;
    setSearchOpen(false);
  };

  useEffect(() => {
    const fromHash = () => {
      const value = window.location.hash.slice(1).split("/")[0];
      if (value === "investigations") setPage("agent-jobs");
      else if (value === "overview" || value === "staffing" || value === "agent-jobs" || value === "llm") setPage(value);
      else if (!value) setPage("overview");
    };
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchOrigin.current = document.activeElement as HTMLElement;
        searchNavigated.current = false;
        setSearchOpen((open) => !open);
      }
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    window.addEventListener("keydown", shortcut);
    return () => {
      window.removeEventListener("hashchange", fromHash);
      window.removeEventListener("keydown", shortcut);
    };
  }, []);

  useEffect(() => {
    mainRef.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }, [page]);

  const searchItems = [
    { label: "Overview", detail: "Account balance, cash forecast and expense mix", page: "overview" as Page },
    { label: "Staffing & payroll", detail: "Team, salaries and payroll export", page: "staffing" as Page },
    { label: "Agent jobs", detail: "Agent activity, outcomes and approvals", page: "agent-jobs" as Page },
    { label: "LLM", detail: "Chat with Granite", page: "llm" as Page },
    ...data.jobs.map(job => ({ label: job.title, detail: `${job.action.replaceAll("_", " ")} · ${job.status}`, page: "agent-jobs" as Page })),
    ...data.employees.map((person) => ({ label: person.name, detail: `${person.role} · ${person.team}`, page: "staffing" as Page })),
  ].filter((item) => `${item.label} ${item.detail}`.toLowerCase().includes(search.toLowerCase().trim()));

  return (
    <>
      <a id="lantern-skip-link" href="#main-content" onClick={event=>{event.preventDefault();mainRef.current?.focus();}} className="fixed top-3 left-3 z-50 -translate-y-24 rounded-md bg-primary px-4 py-3 text-sm text-primary-foreground focus:translate-y-0">Skip to content</a>
      <Sidebar id="lantern-sidebar" className="border-r" aria-label="Primary navigation">
        <SidebarHeader className="gap-6 px-3 pt-6 pb-4">
          <div className="flex items-center gap-3 px-2">
            <span className="flex size-9 items-center justify-center rounded-lg border border-primary/50 bg-primary/10 text-success"><Sparkles className="size-4" aria-hidden="true" /></span>
            <div><span className="text-base font-medium text-foreground">Lantern</span><p className="mt-1 font-mono text-[11px] text-muted-foreground">Autonomous CFO</p></div>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button id="lantern-company-switcher" variant="outline" className="h-auto min-h-16 w-full justify-start gap-3 bg-card px-3 py-3" aria-label="Select company">
                <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-warning-border bg-warning-muted text-xs font-medium text-warning">DB</span>
                <span className="min-w-0 flex-1 text-left"><strong id="lantern-company-name" className="block truncate text-sm font-medium">Company workspace</strong><span className="mt-1 block text-xs font-normal text-muted-foreground">Bank records · EUR</span></span>
                <ChevronDown className="size-4 text-muted-foreground" aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent id="lantern-company-list" align="start" className="w-[--radix-dropdown-menu-trigger-width] min-w-56">
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Company workspace</DropdownMenuLabel>
              <DropdownMenuRadioGroup value="company">
                <DropdownMenuRadioItem id="lantern-company-company" value="company">Company workspace</DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarHeader>
        <SidebarContent className="px-1">
          <SidebarGroup>
            <SidebarGroupLabel className="font-mono text-[11px] font-normal uppercase tracking-wide">Workspace</SidebarGroupLabel>
            <SidebarMenu className="gap-1.5">
              {navigation.map(({ page: target, label, icon: Icon }) => (
                <SidebarMenuItem key={target}>
                  <SidebarMenuButton id={`lantern-nav-${target}`} isActive={page === target} onClick={() => navigate(target)} aria-current={page === target ? "page" : undefined} className="h-11 gap-3 px-3 text-sm">
                    <Icon className="size-4" aria-hidden="true" /><span>{label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
              <SidebarMenuItem><SidebarMenuButton disabled className="h-11 gap-3 px-3 text-sm" aria-label="Connections, coming soon"><Activity className="size-4" aria-hidden="true" /><span>Connections</span></SidebarMenuButton></SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter className="gap-5 px-3 pb-5">
          <Card className="gap-3 bg-sidebar py-4 shadow-none">
            <CardContent className="space-y-3 px-3">
              <div className="flex items-center gap-2 text-xs"><span className="size-2 rounded-full bg-emerald-400" aria-hidden="true" />Company workspace</div>
              <p className="text-xs leading-5 text-muted-foreground">Company records through {data.asOf}</p>
              <p className="font-mono text-[10px] leading-5 text-muted-foreground">BANK · PAYROLL · DECISIONS</p>
            </CardContent>
          </Card>
          <Button variant="ghost" className="h-auto justify-start gap-3 px-1 py-1 text-left" onClick={() => setNotice({ title: "Workspace access", description: "This workspace requires a shared dashboard login. It provides read-only access to company records. Approval actions are handled by the agent through its configured channels." })}>
            <Avatar className="size-9"><AvatarFallback className="bg-primary text-xs text-primary-foreground">DA</AvatarFallback></Avatar>
            <span><span className="block text-xs font-medium">Workspace viewer</span><span className="mt-1 block text-[11px] font-normal text-muted-foreground">Read-only</span></span>
          </Button>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset id="lantern-workspace" className="min-w-0 bg-background">
        <header id="lantern-topbar" className="sticky top-0 z-20 flex h-16 shrink-0 items-center gap-4 border-b bg-background/95 px-4 backdrop-blur-sm md:px-8">
          <SidebarTrigger className="md:hidden" aria-label="Open navigation" />
          <div className="flex min-w-0 flex-1 items-center gap-3 text-xs">
            <span className="hidden text-muted-foreground lg:inline">Finance workspace</span><span className="hidden text-muted-foreground lg:inline" aria-hidden="true">/</span>
            <strong className="truncate font-medium">{page === "overview" ? data.asOf : page === "staffing" ? "People" : page === "llm" ? "LLM" : "Agent jobs"}</strong>
          </div>
          <Button id="lantern-search-trigger" variant="outline" onClick={openSearch} className="hidden h-10 w-full max-w-sm justify-start gap-3 bg-card text-muted-foreground lg:flex">
            <Search className="size-4" aria-hidden="true" /><span className="flex-1 text-left text-xs font-normal">Search transactions, people…</span><kbd className="rounded border px-1.5 py-0.5 font-mono text-[10px]">⌘ K</kbd>
          </Button>
          <Button id="lantern-search-compact" variant="ghost" size="icon" className="size-10 shrink-0 lg:hidden" aria-label="Search workspace" onClick={openSearch}><Search className="size-4" aria-hidden="true" /></Button>
          <ThemeToggle />
          <span className="hidden items-center gap-2 text-[11px] text-muted-foreground xl:flex"><span className="size-1.5 rounded-full bg-emerald-400" aria-hidden="true" />Company records</span>
          <Button id="lantern-notifications" variant="outline" size="icon" aria-label="Notifications" className="size-10 shrink-0" onClick={() => setNotice({title:"Workspace notifications",description:`${data.undocumentedPayments} undocumented payments and ${data.pendingApprovals} pending approvals. Open Agent jobs to review the agent’s work.`})}><Bell className="size-4" aria-hidden="true" /></Button>
        </header>

        <section id="main-content" ref={mainRef} tabIndex={-1} aria-label={page === "overview" ? "Overview" : page === "staffing" ? "Staffing and payroll" : page === "llm" ? "LLM chat" : "Agent jobs"} className="mx-auto w-full max-w-[1600px] min-w-0 px-4 pt-7 pb-28 outline-none md:px-8 md:pt-8 md:pb-10">
          {page === "overview" ? (
            <div id="lantern-overview" className="space-y-6">
              <section className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
                <div className="space-y-2"><p className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">Your autonomous finance workspace</p><h1 className="text-3xl font-medium tracking-tight">Your finance overview</h1><p className="max-w-2xl text-sm leading-6 text-muted-foreground">{data.year.transactions.toLocaleString("en-US")} bank transactions. {attention} items need attention. Every decision connected to its evidence.</p></div>
                <Button id="lantern-refresh" className="min-h-11 shrink-0 gap-2" disabled={refreshing} onClick={() => startRefresh(() => router.refresh())}><Activity className="size-4" aria-hidden="true" />{refreshing ? "Refreshing…" : "Refresh records"}</Button>
              </section>

              <section id="lantern-financial-summary" aria-label="Financial summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {[
                  { label: "Available balance", value: currency(data.closingBalance), note: `As of ${data.asOf}`, trend: "Sum of recorded bank bookings", icon: Wallet, tone: "positive" },
                  { label: "Money in", value: compact(data.year.totalRevenue), note: `Recorded inflows · ${data.yearLabel}`, trend: "Company bank transactions", icon: ArrowDownLeft, tone: "neutral" },
                  { label: "Money out", value: compact(data.year.totalExpenses), note: `Recorded outflows · ${data.yearLabel}`, trend: `${data.year.expenses.length} categories tracked`, icon: ArrowUpRight, tone: "neutral" },
                  { label: "Needs attention", value: String(attention), note: "Undocumented payments + approvals", trend: `${data.pendingApprovals} awaiting approval`, icon: Activity, tone: "warning" },
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

              <Card id="lantern-priority-inbox" aria-labelledby="priority-title" className="gap-5">
                <CardHeader className="flex flex-row justify-between gap-4"><div className="space-y-1.5"><CardTitle id="priority-title" className="text-base font-medium">Priority inbox</CardTitle><CardDescription>Evidence gathered by the autonomous finance loop</CardDescription></div><Badge variant="outline" className="h-fit shrink-0 border-warning-border text-warning">{attention} open</Badge></CardHeader>
                <CardContent className="divide-y">
                  <PriorityItem label="Documents" title={`${data.undocumentedPayments} payments without invoice documents`} description="Counted from bank bookings with an invoice reference that are not documented." action="Review jobs" tone="red" onClick={() => navigate("agent-jobs")} />
                  <PriorityItem label="Approvals" title={`${data.pendingApprovals} pending approvals`} description="Review the agent’s reasoning in its recorded jobs." action="View jobs" tone="orange" onClick={() => navigate("agent-jobs")} />
                </CardContent>
              </Card>

              <section className="grid gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]" aria-label="Finance loop and payroll">
                <Card id="lantern-data-source" className="gap-5"><CardHeader><CardTitle className="text-base font-medium">Connected records</CardTitle><CardDescription>Company data through {data.asOf}</CardDescription></CardHeader><CardContent className="space-y-3 text-sm text-muted-foreground"><p>Bank bookings, employee directory and agent decisions are loaded from the company database.</p><p>Refresh records to see new bookings and decisions.</p></CardContent></Card>
                <Card id="lantern-recorded-payroll" className="gap-4"><CardHeader><CardTitle className="text-base font-medium">Latest recorded payroll</CardTitle><CardDescription>Latest month with salary bookings</CardDescription></CardHeader><CardContent className="space-y-4"><div className="flex items-center justify-between gap-3"><div><span className="text-xs text-muted-foreground">{data.employees.filter(employee => employee.salary !== null).length} recorded recipients</span><strong className="mt-1 block font-mono text-2xl font-medium tracking-tight">{currency(data.monthlyPayroll)}</strong></div><div className="flex -space-x-2" aria-label="Team members">{data.employees.map(employee=><Avatar key={employee.id} className="size-8 border-2 border-card"><AvatarFallback className="bg-secondary text-[10px]">{employee.initials}</AvatarFallback></Avatar>)}</div></div><Button variant="secondary" size="sm" className="min-h-10" onClick={() => navigate("staffing")}>Review staffing</Button></CardContent></Card>
              </section>
            </div>
          ) : page === "staffing" ? <Staffing data={data} onBack={() => navigate("overview")} /> : page === "llm" ? null : <AgentJobs jobs={data.jobs} />}
          <div hidden={page !== "llm"}><LlmChat active={page === "llm"} /></div>
        </section>

        <nav id="lantern-mobile-navigation" aria-label="Mobile navigation" className="fixed right-0 bottom-0 left-0 z-30 grid grid-cols-4 gap-2 border-t bg-sidebar/95 px-3 pt-2 pb-[max(.5rem,env(safe-area-inset-bottom))] backdrop-blur-sm md:hidden">
          {navigation.map(({page:target,label,icon:Icon})=><Button key={target} id={`lantern-mobile-${target}`} variant={page===target?"secondary":"ghost"} aria-current={page===target?"page":undefined} onClick={()=>navigate(target)} className="h-auto min-h-14 flex-col gap-1 text-[11px]"><Icon className="size-4" aria-hidden="true" />{label}</Button>)}
        </nav>
      </SidebarInset>

      <Dialog open={searchOpen} onOpenChange={setSearchOpen}>
        <DialogContent id="lantern-search-dialog" onCloseAutoFocus={event=>{event.preventDefault();if(searchNavigated.current){mainRef.current?.focus();searchNavigated.current=false;}else{searchOrigin.current?.focus();}}} className="max-w-xl"><DialogHeader><DialogTitle>Search your workspace</DialogTitle><DialogDescription>Find views, people and agent jobs.</DialogDescription></DialogHeader><Input id="lantern-search-input" aria-label="Search workspace" value={search} onChange={event=>setSearch(event.target.value)} placeholder="Search transactions, people…" /><div className="max-h-80 space-y-1 overflow-y-auto" aria-live="polite">{searchItems.length ? searchItems.map(item=><Button key={item.label} variant="ghost" className="h-auto min-h-16 w-full justify-start px-3 py-3 text-left" onClick={()=>navigate(item.page)}><span><span className="block text-sm">{item.label}</span><span className="mt-1 block whitespace-normal text-xs font-normal text-muted-foreground">{item.detail}</span></span></Button>):<p className="p-3 text-sm text-muted-foreground">No matching records.</p>}</div></DialogContent>
      </Dialog>
      <Dialog open={notice !== null} onOpenChange={open=>{if(!open)setNotice(null);}}><DialogContent onCloseAutoFocus={event=>{event.preventDefault();noticeOrigin.current?.focus();}}><DialogHeader><DialogTitle>{notice?.title}</DialogTitle><DialogDescription className="pt-2 leading-7">{notice?.description}</DialogDescription></DialogHeader><Button variant="secondary" onClick={()=>setNotice(null)}>Close</Button></DialogContent></Dialog>
    </>
  );
}

function PriorityItem({ label, title, description, action, tone, onClick }: {label:string;title:string;description:string;action:string;tone:"red"|"orange"|"neutral";onClick:()=>void}) {
  return <article className="flex flex-col justify-between gap-4 py-5 first:pt-0 last:pb-0 sm:flex-row sm:items-center"><div className="space-y-2"><Badge variant="outline" className={`font-normal ${tone==="red"?"border-danger-border text-danger":tone==="orange"?"border-warning-border text-warning":"text-muted-foreground"}`}>{label}</Badge><h3 className="text-sm font-medium">{title}</h3><p className="max-w-3xl text-xs leading-6 text-muted-foreground">{description}</p></div><Button variant="outline" size="sm" className="min-h-10 w-fit shrink-0" onClick={onClick}>{action}</Button></article>;
}
