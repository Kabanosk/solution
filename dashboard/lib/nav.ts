// Sidebar navigation model and hash routing. Pure data so it can be tested without React.
import type { SetupStepId } from "./onboarding-model";
import type { TaskStatus } from "./scout-model";

export type View =
  | "overview" | "tasks" | "chat"
  | "transactions" | "staffing"
  | "connections" | "audit" | "policies" | "llm-usage" | "demo"
  /** Guided workspace setup. Reached by #setup and the Connections button, not a sidebar item. */
  | "setup";

/** "inbox": waiting on / escalated to the CEO (or a pending approval); "mine": owned by the CEO. */
export type TaskScope = "inbox" | "mine";
/** Filters of the one task list. Sidebar items are presets of it. */
export type TaskFilter = { scope?: TaskScope; loop?: string; status?: TaskStatus; owner?: string };

/** Where the dashboard is: a view, the task list's filter and an open task. */
export type Route = { view: View; filter?: TaskFilter; taskId?: string; setupStep?: SetupStepId };

export type IconName =
  | "inbox" | "list-todo" | "refresh" | "layout" | "receipt" | "users"
  | "plug" | "scroll" | "shield" | "message" | "cpu" | "play";

export type CountKey = "inbox" | "openTasks" | "loop";

export type NavItem = {
  id: string;
  label: string;
  href: string;
  icon: IconName;
  route: Route;
  countKey?: CountKey;
  /** agent_tasks.loop value for loop items. */
  loop?: string;
  /** Only shown when the demo scenarios are enabled (SCOUT_DEMO=1). */
  demoOnly?: boolean;
};

export type NavSection = { section: "primary" | "Loops" | "Workspace"; collapsible: boolean; items: NavItem[] };

const FILTER_KEYS = ["scope", "loop", "status", "owner"] as const;

const item = (id: string, label: string, icon: IconName, route: Route, extra: Partial<NavItem> = {}): NavItem =>
  ({ id, label, href: `/${hashFromRoute(route)}`, icon, route, ...extra });
const view = (id: View, label: string, icon: IconName, extra: Partial<NavItem> = {}) => item(id, label, icon, { view: id }, extra);
const loopItem = (loop: string, label: string) =>
  item(`loop-${loop.replaceAll("_", "-")}`, label, "refresh", { view: "tasks", filter: { loop } }, { countKey: "loop", loop });

export const NAV: NavSection[] = [
  { section: "primary", collapsible: false, items: [
    view("overview", "Overview", "layout"),
    view("chat", "Scout chat", "message"),
    view("tasks", "Tasks", "list-todo", { countKey: "openTasks" }),
    view("transactions", "Transactions", "receipt"),
  ] },
  { section: "Loops", collapsible: true, items: [
    loopItem("invoicing", "Invoicing"),
    loopItem("cost_control", "Cost control"),
    loopItem("liquidity", "Liquidity"),
    loopItem("strategy", "Strategy"),
    loopItem("forecast", "Forecast"),
    loopItem("chat", "Chat"),
  ] },
  { section: "Workspace", collapsible: true, items: [
    view("connections", "Connections", "plug"),
    view("audit", "Audit log", "scroll"),
    view("policies", "Access policies", "shield"),
    view("llm-usage", "LLM usage", "cpu"),
  ] },
];

export const NAV_ITEMS: NavItem[] = NAV.flatMap(section => section.items);
const VIEWS = new Set<string>(["overview", "tasks", "chat", "transactions", "staffing", "connections", "audit", "policies", "llm-usage", "demo", "setup"]);

/** Route to a URL hash: `#overview`, `#tasks?loop=invoicing&status=done`, `#task/SC-110`. */
export function hashFromRoute(route: Route): string {
  if (route.taskId) return `#task/${encodeURIComponent(route.taskId)}`;
  const params = new URLSearchParams();
  for (const key of FILTER_KEYS) { const value = route.filter?.[key]; if (value) params.set(key, value); }
  if (route.view === "setup" && route.setupStep) params.set("step", route.setupStep);
  const query = route.view === "tasks" || route.view === "setup" ? params.toString() : "";
  return `#${route.view}${query ? `?${query}` : ""}`;
}

/** Hash (with or without '#') to a route. Old views keep working: #forecast is part of the overview, #inbox,
 *  #my-tasks and #loop-* are presets of the task list, #investigations / #agent-jobs open it, #llm opens chat. */
export function routeFromHash(hash: string): Route {
  const raw = hash.replace(/^#/, "");
  if (!raw) return { view: "chat" };
  const [path, query = ""] = raw.split("?", 2);
  if (path.startsWith("task/")) {
    const id = decodeURIComponent(path.slice(5));
    if (id) return { view: "tasks", taskId: id };
  }
  if (path === "inbox") return { view: "tasks", filter: { scope: "inbox" } };
  if (path === "my-tasks") return { view: "tasks", filter: { scope: "mine" } };
  if (path.startsWith("loop-")) return { view: "tasks", filter: { loop: path.slice(5).replaceAll("-", "_") } };
  if (path === "investigations" || path === "agent-jobs" || path.startsWith("agent-jobs/")) return { view: "tasks" };
  if (path === "llm") return { view: "chat" };
  if (path === "setup") {
    const step = new URLSearchParams(query).get("step");
    return step && ["bank", "invoicing", "ksef", "email", "slack", "network"].includes(step) ? { view: "setup", setupStep: step as SetupStepId } : { view: "setup" };
  }
  if (path === "tasks") {
    const params = new URLSearchParams(query);
    const filter: TaskFilter = {};
    const scope = params.get("scope");
    if (scope === "inbox" || scope === "mine") filter.scope = scope;
    for (const key of ["loop", "status", "owner"] as const) { const value = params.get(key); if (value) (filter as Record<string, string>)[key] = value; }
    return { view: "tasks", filter };
  }
  return { view: VIEWS.has(path) ? path as View : "overview" };
}

/** The sidebar item a route belongs to: the most specific preset (loop / scope) that matches. */
export function activeItem(route: Route): NavItem | undefined {
  if (route.view !== "tasks") return NAV_ITEMS.find(entry => entry.route.view === route.view);
  const filter = route.filter ?? {};
  if (filter.loop && !filter.scope) return NAV_ITEMS.find(entry => entry.loop === filter.loop);
  return NAV_ITEMS.find(entry => entry.id === "tasks");
}

/** Two-key "g" shortcuts. */
export const GO_SHORTCUTS: Record<string, Route> = {
  o: { view: "overview" }, t: { view: "tasks" }, i: { view: "tasks", filter: { scope: "inbox" } }, c: { view: "chat" },
};

export const VIEW_LABEL: Record<View, string> = {
  overview: "Overview", tasks: "Tasks", chat: "Scout chat", transactions: "Transactions", staffing: "Staffing",
  connections: "Connections", audit: "Audit log", policies: "Access policies", "llm-usage": "LLM usage", demo: "Demo", setup: "Setup",
};
