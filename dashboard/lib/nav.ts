// Sidebar navigation model. Pure data so it can be tested without React.
export type View =
  | "inbox" | "my-tasks" | "tasks" | "chat"
  | "loop-invoicing" | "loop-cost-control" | "loop-liquidity" | "loop-chat"
  | "overview" | "transactions" | "forecast" | "staffing"
  | "connections" | "audit" | "policies" | "llm-usage";

export type IconName =
  | "inbox" | "user-check" | "list-todo" | "refresh" | "layout" | "receipt" | "chart" | "users"
  | "plug" | "scroll" | "shield" | "message" | "cpu";

export type CountKey = "inbox" | "myTasks" | "openTasks" | "loop";

export type NavItem = {
  id: View;
  label: string;
  href: string;
  icon: IconName;
  countKey?: CountKey;
  /** agent_tasks.loop value for loop items. */
  loop?: string;
};

export type NavSection = { section: "primary" | "Loops" | "Finance" | "Workspace"; collapsible: boolean; items: NavItem[] };

const item = (id: View, label: string, icon: IconName, extra: Partial<NavItem> = {}): NavItem =>
  ({ id, label, href: `/#${id}`, icon, ...extra });

export const NAV: NavSection[] = [
  { section: "primary", collapsible: false, items: [
    item("inbox", "Inbox", "inbox", { countKey: "inbox" }),
    item("my-tasks", "My tasks", "user-check", { countKey: "myTasks" }),
    item("tasks", "Agent tasks", "list-todo", { countKey: "openTasks" }),
    item("chat", "Scout chat", "message"),
  ] },
  { section: "Loops", collapsible: true, items: [
    item("loop-invoicing", "Invoicing", "refresh", { countKey: "loop", loop: "invoicing" }),
    item("loop-cost-control", "Cost control", "refresh", { countKey: "loop", loop: "cost_control" }),
    item("loop-liquidity", "Liquidity", "refresh", { countKey: "loop", loop: "liquidity" }),
    item("loop-chat", "Chat", "refresh", { countKey: "loop", loop: "chat" }),
  ] },
  { section: "Finance", collapsible: true, items: [
    item("overview", "Overview", "layout"),
    item("transactions", "Transactions", "receipt"),
    item("forecast", "Forecast", "chart"),
    item("staffing", "Staffing", "users"),
  ] },
  { section: "Workspace", collapsible: true, items: [
    item("connections", "Connections", "plug"),
    item("audit", "Audit log", "scroll"),
    item("policies", "Access policies", "shield"),
    item("llm-usage", "LLM usage", "cpu"),
  ] },
];

export const NAV_ITEMS: NavItem[] = NAV.flatMap(section => section.items);
const VIEWS = new Set<string>(NAV_ITEMS.map(entry => entry.id));

/** Hash (with or without '#') to view. Legacy #investigations / #agent-jobs open Agent tasks, #llm opens Scout chat. */
export function viewFromHash(hash: string): View {
  const value = hash.replace(/^#/, "");
  if (value === "investigations" || value === "agent-jobs" || value.startsWith("agent-jobs/")) return "tasks";
  if (value === "llm") return "chat";
  return VIEWS.has(value) ? value as View : "overview";
}

/** Two-key "g" shortcuts. */
export const GO_SHORTCUTS: Record<string, View> = { t: "tasks", i: "inbox" };

export function navItem(view: View): NavItem {
  return NAV_ITEMS.find(entry => entry.id === view)!;
}
