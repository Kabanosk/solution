import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export const eur = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
export const eurCents = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "22 Nov" / "22 Nov 2026" from "2026-11-22"; built by hand so server and browser print the same. */
export function day(date: string, withYear = false) {
  const [year, month, dayOfMonth] = date.slice(0, 10).split("-").map(Number);
  return `${dayOfMonth} ${MONTHS[month - 1]}${withYear ? ` ${year}` : ""}`;
}

/** The overview's card: 1px border, a 13 px title row, content below. */
export function Panel({ id, title, meta, actions, children, className }: {
  id: string; title: ReactNode; meta?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return <section id={id} aria-labelledby={`${id}-title`} className={cn("flex min-w-0 flex-col overflow-hidden rounded-xl border bg-card", className)}>
    <header className="flex min-h-11 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b px-4 py-2">
      <div className="min-w-0">
        <h2 id={`${id}-title`} className="text-[13px] font-medium">{title}</h2>
        {meta && <p className="text-[11px] text-muted-foreground">{meta}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
    {children}
  </section>;
}

/** Segmented control: a radio group of 2–3 options. */
export function Segmented<T extends string>({ id, label, value, options, onChange }: {
  id: string; label: string; value: T; options: { value: T; label: string; title?: string }[]; onChange: (value: T) => void;
}) {
  return <div id={id} role="radiogroup" aria-label={label} className="flex max-w-full rounded-lg border bg-muted/40 p-0.5"
    onKeyDown={event => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const index = options.findIndex(option => option.value === value);
      const next = options[(index + (event.key === "ArrowRight" ? 1 : options.length - 1)) % options.length];
      onChange(next.value);
      requestAnimationFrame(() => document.getElementById(`${id}-${next.value}`)?.focus());
    }}>
    {options.map(option => <button key={option.value} id={`${id}-${option.value}`} type="button" role="radio" aria-checked={option.value === value}
      tabIndex={option.value === value ? 0 : -1} title={option.title} onClick={() => onChange(option.value)}
      className={cn("h-7 min-w-0 truncate rounded-md px-2.5 text-[12px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
        option.value === value && "bg-background font-medium text-foreground shadow-xs")}>{option.label}</button>)}
  </div>;
}

export function Swatch({ color, dashed = false }: { color: string; dashed?: boolean }) {
  return dashed
    ? <span aria-hidden="true" className="inline-block w-3 shrink-0 border-t-2 border-dashed" style={{ borderColor: color }} />
    : <span aria-hidden="true" className="inline-block size-2 shrink-0 rounded-[2px]" style={{ backgroundColor: color }} />;
}
