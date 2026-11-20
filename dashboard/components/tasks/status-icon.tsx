import { Check, Clock, X } from "lucide-react";
import type { TaskStatus } from "@/lib/scout-model";
import { STATUS_LABEL } from "@/lib/scout-model";
import { cn } from "@/lib/utils";

/** Linear-style status glyphs. */
export function StatusIcon({ status, className }: { status: TaskStatus; className?: string }) {
  const base = cn("inline-flex size-3.5 shrink-0 items-center justify-center rounded-full", className);
  const label = STATUS_LABEL[status];
  switch (status) {
    case "in_progress":
      return <span role="img" aria-label={label} title={label} className={cn(base, "border-[1.5px] border-amber-500 p-[1.5px]")}>
        <span className="size-full rounded-full" style={{ background: "conic-gradient(var(--color-amber-500) 0 50%, transparent 50% 100%)" }} />
      </span>;
    case "waiting":
      return <Clock role="img" aria-label={label} className={cn("size-3.5 shrink-0 text-amber-600 dark:text-amber-400", className)} />;
    case "escalated":
      return <span role="img" aria-label={label} title={label} className={cn(base, "bg-red-600 text-[9px] font-bold leading-none text-white")}>!</span>;
    case "todo":
      return <span role="img" aria-label={label} title={label} className={cn(base, "border-[1.5px] border-dashed border-muted-foreground")} />;
    case "done":
      return <span role="img" aria-label={label} title={label} className={cn(base, "bg-emerald-600 text-white")}><Check className="size-2.5" strokeWidth={3} /></span>;
    case "cancelled":
      return <span role="img" aria-label={label} title={label} className={cn(base, "bg-muted-foreground/60 text-white")}><X className="size-2.5" strokeWidth={3} /></span>;
  }
}

export function PriorityBars({ value }: { value: 1 | 2 | 3 }) {
  return <span role="img" aria-label={`Priority ${value} of 3`} className="inline-flex h-3 shrink-0 items-end gap-[2px]">
    {[1, 2, 3].map(bar => <span key={bar} className={cn("w-[3px] rounded-[1px]", bar <= value ? "bg-foreground/70" : "bg-foreground/15")} style={{ height: 4 + bar * 2.5 }} />)}
  </span>;
}
