import { MARK_BACKGROUND, MARK_PATH, MARK_TRANSFORM } from "@/lib/brand-icon";
import { cn } from "@/lib/utils";

/** The deck's hat mark, white on a #141416 rounded square (radius 22%). */
export function ScoutMark({ size = 24, className }: { size?: number; className?: string }) {
  return <span aria-hidden="true" className={cn("inline-flex shrink-0 items-center justify-center text-white", className)}
    style={{ width: size, height: size, borderRadius: size * 0.22, background: MARK_BACKGROUND }}>
    <svg viewBox="0 0 24 24" width={size * 0.78} height={size * 0.78}>
      <path fill="currentColor" fillRule="evenodd" transform={MARK_TRANSFORM} d={MARK_PATH} />
    </svg>
  </span>;
}

export function ScoutLockup({ tagline = true, className }: { tagline?: boolean; className?: string }) {
  return <span className={cn("inline-flex items-center gap-2.5", className)}>
    <ScoutMark size={28} />
    <span className="leading-tight">
      <span className="block text-sm font-semibold tracking-tight">Scout</span>
      {tagline && <span className="block text-[11px] text-muted-foreground">Your autonomous CFO</span>}
    </span>
  </span>;
}
