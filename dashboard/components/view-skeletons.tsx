import { Skeleton } from "@/components/ui/skeleton";

// Loading placeholders shaped like each view, so content replaces them without moving the layout.
export type SkeletonView = "overview" | "tasks" | "task" | "transactions" | "staffing" | "connections" | "audit" | "llm-usage" | "chat" | "policies";

const Title = ({ wide = false }: { wide?: boolean }) => <div className="space-y-2"><Skeleton className={wide ? "h-8 w-80 max-w-full" : "h-7 w-48"} /><Skeleton className="h-4 w-[26rem] max-w-full" /></div>;
const Rows = ({ count, height = "h-10" }: { count: number; height?: string }) =>
  <>{Array.from({ length: count }, (_, row) => <div key={row} className={`flex ${height} items-center gap-3 border-b px-4 last:border-b-0`}>
    <Skeleton className="h-3 w-16" /><Skeleton className="h-3 flex-1" style={{ maxWidth: `${55 + ((row * 37) % 40)}%` }} /><Skeleton className="ml-auto h-3 w-20" />
  </div>)}</>;

function TableSkeleton({ rows, header = true }: { rows: number; header?: boolean }) {
  return <div className="overflow-hidden rounded-xl border bg-card">
    {header && <div className="flex h-10 items-center gap-6 border-b px-4">{[16, 28, 24, 16, 20].map((w, i) => <Skeleton key={i} className="h-3" style={{ width: `${w * 3}px` }} />)}</div>}
    <Rows count={rows} height="h-[41px]" />
  </div>;
}

export function ViewSkeleton({ view }: { view: SkeletonView }) {
  switch (view) {
    case "overview": return <div className="space-y-6">
      <div className="flex items-end justify-between gap-5"><div className="space-y-3"><Skeleton className="h-3 w-48" /><Skeleton className="h-9 w-80 max-w-full" /><Skeleton className="h-4 w-[28rem] max-w-full" /></div><Skeleton className="hidden h-11 w-40 sm:block" /></div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">{[0, 1, 2, 3, 4].map(card => <Skeleton key={card} className="h-[150px] rounded-xl" />)}</div>
      <Skeleton className="h-[540px] rounded-xl" />
    </div>;
    case "tasks": return <div className="overflow-hidden rounded-xl border bg-card">
      <div className="space-y-3 border-b px-4 py-3">
        <div className="flex items-center gap-3"><div className="mr-auto space-y-1.5"><Skeleton className="h-4 w-24" /><Skeleton className="h-3 w-72 max-w-full" /></div><Skeleton className="h-8 w-48 rounded-lg" /></div>
        <div className="flex gap-2"><Skeleton className="h-8 w-28" /><Skeleton className="h-8 w-28" /><Skeleton className="h-8 w-28" /></div>
      </div>
      {[3, 2, 6].map((rows, group) => <div key={group}><div className="flex h-9 items-center gap-2 border-b bg-muted/40 px-4"><Skeleton className="size-3.5 rounded-full" /><Skeleton className="h-3 w-20" /></div><Rows count={rows} /></div>)}
    </div>;
    case "task": return <div className="space-y-5">
      <Skeleton className="h-4 w-40" />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-5">
          <div className="space-y-2"><Skeleton className="h-7 w-72" /><Skeleton className="h-4 w-96 max-w-full" /><Skeleton className="h-3 w-48" /></div>
          <div className="rounded-xl border bg-card"><div className="border-b px-4 py-2.5"><Skeleton className="h-4 w-24" /></div>
            <div className="space-y-4 px-4 py-4">{[0, 1, 2, 3].map(row => <div key={row} className="flex items-center gap-3"><Skeleton className="size-6 rounded-full" /><Skeleton className="h-4 flex-1" /><Skeleton className="h-3 w-10" /></div>)}</div></div>
        </div>
        <div className="h-fit space-y-3 rounded-xl border bg-card px-4 py-4">{[0, 1, 2, 3, 4, 5].map(row => <div key={row} className="grid grid-cols-[96px_1fr] gap-2"><Skeleton className="h-4 w-16" /><Skeleton className="h-4" /></div>)}</div>
      </div>
    </div>;
    case "transactions": return <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3"><Title /><div className="flex gap-2"><Skeleton className="h-6 w-24 rounded-full" /><Skeleton className="h-6 w-20 rounded-full" /><Skeleton className="h-8 w-48" /></div></div>
      <TableSkeleton rows={14} />
    </div>;
    case "audit": return <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3"><Title /><Skeleton className="h-8 w-24" /></div>
      <TableSkeleton rows={14} />
    </div>;
    case "staffing": return <div className="space-y-6">
      <div className="flex items-end justify-between gap-4"><div className="space-y-2"><Skeleton className="h-3 w-40" /><Skeleton className="h-8 w-64" /><Skeleton className="h-4 w-[30rem] max-w-full" /></div><Skeleton className="h-11 w-32" /></div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map(card => <Skeleton key={card} className="h-[118px] rounded-xl" />)}</div>
      <TableSkeleton rows={6} />
    </div>;
    case "connections": return <div className="space-y-4">
      <Title />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{[0, 1, 2, 3, 4].map(card => <div key={card} className="space-y-4 rounded-xl border bg-card p-6">
        <div className="flex items-start justify-between"><div className="flex items-center gap-3"><Skeleton className="size-8" /><div className="space-y-1.5"><Skeleton className="h-4 w-28" /><Skeleton className="h-3 w-40" /></div></div><Skeleton className="h-5 w-20 rounded-full" /></div>
        <Skeleton className="h-3 w-48" />
      </div>)}</div>
    </div>;
    case "policies": return <div className="space-y-4"><Title /><Skeleton className="h-64 rounded-xl" /></div>;
    case "llm-usage": return <div className="space-y-4">
      <div className="flex items-end justify-between gap-3"><Title /><Skeleton className="h-8 w-48 rounded-lg" /></div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{[0, 1, 2, 3].map(card => <Skeleton key={card} className="h-[108px] rounded-xl" />)}</div>
      <div className="grid gap-4 xl:grid-cols-2">{[0, 1, 2, 3].map(card => <Skeleton key={card} className="h-[290px] rounded-xl" />)}</div>
    </div>;
    case "chat": return <div className="flex h-[calc(100dvh-3rem-2.5rem)] flex-col overflow-hidden rounded-xl border bg-card">
      <div className="flex items-center gap-3 border-b px-4 py-2.5"><Skeleton className="size-[22px]" /><div className="flex-1 space-y-1"><Skeleton className="h-3.5 w-24" /><Skeleton className="h-3 w-48" /></div></div>
      <div className="flex flex-1 flex-col items-center justify-center gap-4"><Skeleton className="size-10" /><Skeleton className="h-6 w-32" /><Skeleton className="h-4 w-80" /></div>
      <div className="border-t px-4 py-3"><Skeleton className="mx-auto h-[52px] max-w-3xl rounded-xl" /></div>
    </div>;
  }
}

export const SKELETON_VIEWS: SkeletonView[] = ["overview", "tasks", "task", "transactions", "staffing", "connections", "audit", "llm-usage", "chat", "policies"];

/** Hash -> skeleton, for the first paint before React runs (mirrors lib/nav.ts routeFromHash). */
export const SKELETON_FROM_HASH_SCRIPT = `(function(){var h=location.hash.slice(1).split("?")[0],v="overview";
if(h.indexOf("task/")===0)v="task";else if(h==="tasks"||h==="inbox"||h==="my-tasks"||h.indexOf("loop-")===0||h==="investigations"||h.indexOf("agent-jobs")===0)v="tasks";
else if(h==="chat"||h==="llm")v="chat";else if(["transactions","staffing","connections","audit","llm-usage","policies"].indexOf(h)>=0)v=h;
var s=document.currentScript;if(s&&s.parentElement)s.parentElement.setAttribute("data-view",v);})();`;
