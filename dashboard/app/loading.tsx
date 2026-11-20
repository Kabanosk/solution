import { Skeleton } from "@/components/ui/skeleton";

// Streams at once while the company records load: the dashboard's own layout, not a blank page.
export default function Loading() {
  return <div id="scout-loading" role="status" aria-label="Loading company records" className="flex min-h-svh w-full">
    <aside id="scout-loading-sidebar" className="hidden w-64 shrink-0 flex-col gap-5 border-r bg-sidebar px-3 py-4 md:flex">
      <Skeleton className="h-6 w-36" />
      {[4, 4, 2, 4].map((rows, section) => <div key={section} className="space-y-2">
        {section > 0 && <Skeleton className="h-3 w-16" />}
        {Array.from({ length: rows }, (_, row) => <Skeleton key={row} className="h-5 w-full max-w-44" />)}
      </div>)}
    </aside>
    <div className="min-w-0 flex-1">
      <div className="flex h-12 items-center gap-3 border-b px-4 md:px-6"><Skeleton className="size-6" /><Skeleton className="h-4 w-40" /><div className="flex-1" /><Skeleton className="hidden h-8 w-64 lg:block" /><Skeleton className="size-8" /></div>
      <div className="mx-auto max-w-[1600px] space-y-6 px-4 pt-7 md:px-8">
        <div className="space-y-3"><Skeleton className="h-3 w-48" /><Skeleton className="h-8 w-80 max-w-full" /><Skeleton className="h-4 w-[28rem] max-w-full" /></div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">{[0, 1, 2, 3, 4].map(card => <Skeleton key={card} className="h-[150px] rounded-xl" />)}</div>
        <Skeleton className="h-[440px] rounded-xl" />
        <span className="sr-only">Loading company records…</span>
      </div>
    </div>
  </div>;
}
