import Link from "next/link";
import type { ReactNode } from "react";
import { ScoutMark } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";

export function LegalDocument({ title, children }: { title: string; children: ReactNode }) {
  return <main id="scout-legal-page" className="min-h-dvh bg-background px-5 py-8 text-foreground sm:px-8">
    <div id="scout-legal-content" className="mx-auto max-w-3xl">
      <header id="scout-legal-header" className="mb-12 flex items-center justify-between gap-4">
        <Link id="scout-legal-home" href="/" className="flex min-h-11 items-center gap-2.5 rounded focus-visible:outline-2 focus-visible:outline-ring" aria-label="Scout home"><ScoutMark size={28} /><span className="font-semibold">Scout</span></Link>
        <ThemeToggle />
      </header>
      <article id="scout-legal-article" className="space-y-8 text-base leading-7">
        <div id="scout-legal-intro" className="space-y-2 border-b pb-8">
          <h1 id="scout-legal-title" className="text-3xl font-semibold tracking-tight sm:text-4xl">{title}</h1>
          <p id="scout-legal-updated" className="text-sm text-muted-foreground">Effective 21 November 2026</p>
        </div>
        {children}
      </article>
      <footer id="scout-legal-footer" className="mt-14 flex flex-wrap gap-5 border-t py-6 text-sm">
        <Link id="scout-legal-footer-home" href="/" className="underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring">Scout home</Link>
        <Link id="scout-legal-footer-privacy" href="/privacy" className="underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring">Privacy</Link>
        <Link id="scout-legal-footer-terms" href="/terms" className="underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-ring">Terms</Link>
      </footer>
    </div>
  </main>;
}
