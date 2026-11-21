import { Skeleton } from "@/components/ui/skeleton";
import { SKELETON_FROM_HASH_SCRIPT, ViewSkeleton, type SkeletonView } from "@/components/view-skeletons";

// Literal class names so Tailwind generates them: only the skeleton of the view in the URL hash is shown.
const SHOW: Record<SkeletonView, string> = {
  overview: "group-data-[view=overview]/loading:block", tasks: "group-data-[view=tasks]/loading:block",
  task: "group-data-[view=task]/loading:block", transactions: "group-data-[view=transactions]/loading:block",
  staffing: "group-data-[view=staffing]/loading:block", connections: "group-data-[view=connections]/loading:block",
  audit: "group-data-[view=audit]/loading:block", "llm-usage": "group-data-[view=llm-usage]/loading:block",
  chat: "group-data-[view=chat]/loading:block", policies: "group-data-[view=policies]/loading:block",
  demo: "group-data-[view=demo]/loading:block",
};

// Streams at once while the company records load: the dashboard's own layout and the requested view's shape.
export default function Loading() {
  return <div id="scout-loading" role="status" aria-label="Loading company records" data-view="overview" className="group/loading flex min-h-svh w-full">
    <script dangerouslySetInnerHTML={{ __html: SKELETON_FROM_HASH_SCRIPT }} />
    <aside id="scout-loading-sidebar" className="hidden w-64 shrink-0 flex-col gap-5 border-r bg-sidebar px-3 py-4 md:flex">
      <Skeleton className="h-6 w-36" />
      {[4, 4, 2, 4].map((rows, section) => <div key={section} className="space-y-2">
        {section > 0 && <Skeleton className="h-3 w-16" />}
        {Array.from({ length: rows }, (_, row) => <Skeleton key={row} className="h-5 w-full max-w-44" />)}
      </div>)}
    </aside>
    <div className="min-w-0 flex-1">
      <div className="flex h-12 items-center gap-3 border-b px-4 md:px-6"><Skeleton className="size-6" /><Skeleton className="h-4 w-40" /><div className="flex-1" /><Skeleton className="hidden h-8 w-64 lg:block" /><Skeleton className="size-8" /><Skeleton className="size-8" /></div>
      <div className="mx-auto max-w-[1600px] px-4 pt-6 md:px-8 md:pt-7">
        {(Object.keys(SHOW) as SkeletonView[]).map(view => <div key={view} className={`hidden ${SHOW[view]}`}><ViewSkeleton view={view} /></div>)}
        <span className="sr-only">Loading company records…</span>
      </div>
    </div>
  </div>;
}
