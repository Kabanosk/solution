"use client";

import { useEffect, useState } from "react";
import { ChartLine, ChevronDown, ChevronRight, Inbox, LayoutGrid, ListTodo, PanelLeftClose, Plug, Receipt, RefreshCw, ScrollText, Search, ShieldCheck, SquarePen, UserCheck, Users, type LucideIcon } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarHeader, SidebarMenu, SidebarMenuButton, SidebarMenuItem, useSidebar } from "@/components/ui/sidebar";
import { ScoutMark } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { NAV, type IconName, type NavItem, type View } from "@/lib/nav";
import { cn } from "@/lib/utils";

const ICONS: Record<IconName, LucideIcon> = {
  inbox: Inbox, "user-check": UserCheck, "list-todo": ListTodo, refresh: RefreshCw, layout: LayoutGrid, receipt: Receipt,
  chart: ChartLine, users: Users, plug: Plug, scroll: ScrollText, shield: ShieldCheck,
};
const STORAGE_KEY = "scout-nav-collapsed";

export type NavCounts = { inbox: number; myTasks: number; openTasks: number; loops: Record<string, number> };

export function AppSidebar({ active, counts, user, onNavigate, onSearch, onNewTask }: {
  active: View | null; counts: NavCounts; user: { name: string; role: string };
  onNavigate: (view: View) => void; onSearch: () => void; onNewTask: () => void;
}) {
  const { toggleSidebar } = useSidebar();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  useEffect(() => {
    try { setCollapsed(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}")); } catch { /* Defaults stay expanded. */ }
  }, []);
  const toggle = (section: string) => setCollapsed(previous => {
    const next = { ...previous, [section]: !previous[section] };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch { /* Collapse still works without storage. */ }
    return next;
  });
  const count = (item: NavItem) => item.countKey === "loop" ? counts.loops[item.loop!] ?? 0
    : item.countKey === "inbox" ? counts.inbox : item.countKey === "myTasks" ? counts.myTasks : item.countKey === "openTasks" ? counts.openTasks : null;

  return <Sidebar id="scout-sidebar" className="border-r" aria-label="Primary navigation">
    <SidebarHeader className="gap-2 px-2 pt-3 pb-1">
      <div className="flex items-center gap-1">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button id="scout-company-switcher" variant="ghost" className="h-8 min-w-0 flex-1 justify-start gap-2 px-1.5 text-[13px] font-medium" aria-label="Select workspace">
              <ScoutMark size={20} /><span id="scout-company-name" className="truncate">Pizzeria Amalfi</span><ChevronDown className="size-3.5 text-muted-foreground" aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent id="scout-company-list" align="start" className="min-w-56">
            <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Scout · Your autonomous CFO</DropdownMenuLabel>
            <DropdownMenuRadioGroup value="amalfi"><DropdownMenuRadioItem value="amalfi">Pizzeria Amalfi</DropdownMenuRadioItem></DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
        <Button id="scout-sidebar-search" variant="ghost" size="icon" className="size-7" aria-label="Search (⌘K)" title="Search  ⌘K" onClick={onSearch}><Search className="size-4" aria-hidden="true" /></Button>
        <Button id="scout-new-task" variant="ghost" size="icon" className="size-7" aria-label="New task" title="New task" onClick={onNewTask}><SquarePen className="size-4" aria-hidden="true" /></Button>
      </div>
    </SidebarHeader>
    <SidebarContent className="gap-0 px-1">
      {NAV.map(section => {
        const hidden = section.collapsible && collapsed[section.section];
        return <SidebarGroup key={section.section} className="px-1 py-1.5">
          {section.collapsible && <button type="button" onClick={() => toggle(section.section)} aria-expanded={!hidden}
            className="group/section flex h-6 items-center gap-1 rounded px-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase hover:text-foreground">
            {section.section}{hidden ? <ChevronRight className="size-3" aria-hidden="true" /> : <ChevronDown className="size-3 opacity-0 group-hover/section:opacity-100" aria-hidden="true" />}
          </button>}
          {!hidden && <SidebarGroupContent><SidebarMenu className="gap-px">
            {section.items.map(item => {
              const Icon = ICONS[item.icon];
              const value = count(item);
              const current = active === item.id;
              return <SidebarMenuItem key={item.id}>
                <SidebarMenuButton id={`scout-nav-${item.id}`} asChild isActive={current} className="h-7 gap-2 px-2 text-[13px] font-normal hover:bg-sidebar-accent/60 data-[active=true]:bg-sidebar-accent data-[active=true]:font-medium">
                  <a href={item.href} aria-current={current ? "page" : undefined} onClick={event => { if (event.metaKey || event.ctrlKey) return; event.preventDefault(); onNavigate(item.id); }}>
                    <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                    <span className="flex-1 truncate">{item.label}</span>
                    {item.loop && <span className="size-1.5 rounded-full bg-emerald-500" title="Loop running" aria-label="Live" />}
                    {value !== null && value > 0 && <span className={cn("min-w-4 text-right font-mono text-[11px] text-muted-foreground", item.id === "inbox" && "text-foreground")}>{value}</span>}
                  </a>
                </SidebarMenuButton>
              </SidebarMenuItem>;
            })}
          </SidebarMenu></SidebarGroupContent>}
        </SidebarGroup>;
      })}
    </SidebarContent>
    <SidebarFooter className="flex-row items-center gap-1 border-t px-2 py-2">
      <Avatar className="size-6"><AvatarFallback className="bg-primary text-[10px] text-primary-foreground">{user.name.split(" ").map(part => part[0]).join("").slice(0, 2)}</AvatarFallback></Avatar>
      <span className="min-w-0 flex-1 leading-tight"><span className="block truncate text-[13px]">{user.name}</span><span className="block truncate text-[11px] text-muted-foreground">{user.role}</span></span>
      <ThemeToggle className="size-7 border-0 shadow-none" />
      <Button id="scout-sidebar-collapse" variant="ghost" size="icon" className="size-7" aria-label="Collapse sidebar" title="Collapse sidebar  ⌘B" onClick={toggleSidebar}><PanelLeftClose className="size-4" aria-hidden="true" /></Button>
    </SidebarFooter>
  </Sidebar>;
}
