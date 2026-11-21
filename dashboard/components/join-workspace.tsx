"use client";

import { useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { ScoutMark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ThemeToggle } from "@/components/theme-toggle";

type Organization = { name: string; slug: string };
export function JoinWorkspace({ organization, token, valid, slackName, legal }: { organization: Organization; token: string; valid: boolean; slackName: string; legal?: { privacy?: string; terms?: string } }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(valid ? "" : "This invite link is invalid or was already used. Message Scout on Slack for a new one.");
  const errorRef = useRef<HTMLParagraphElement>(null);
  const [first, ...rest] = slackName.trim().split(/\s+/);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError("");
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const response = await fetch("/api/auth/join", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...values, slug: organization.slug, token }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error ?? "Please try again.");
      window.location.assign(body.redirect);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not connect. Please try again."); setPending(false); requestAnimationFrame(() => errorRef.current?.focus()); }
  }
  return <main id="scout-join-page" className="flex min-h-dvh flex-col bg-background px-5 py-6 text-foreground sm:px-8">
    <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-12">
      <header className="mb-8 flex items-center justify-between gap-4">
        <Link href="/" className="flex items-center gap-2.5 text-base font-semibold tracking-tight" aria-label="Scout home"><ScoutMark size={28} />Scout</Link>
        <ThemeToggle />
      </header>
      <Card className="gap-6 border-0 bg-transparent py-0 shadow-none">
        <CardHeader className="gap-3 px-0">
          <p id="scout-join-org" className="text-sm font-medium text-muted-foreground">{organization.name}</p>
          <CardTitle className="text-2xl font-semibold tracking-tight"><h1 id="scout-join-heading">Join {organization.name} workspace</h1></CardTitle>
          <CardDescription className="text-sm leading-6">Create your Scout account. It is linked to your Slack account, so you can also just DM Scout there. Already use Scout elsewhere? Sign up with the same email and password to add this workspace.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 px-0">
          <form id="scout-join-form" onSubmit={submit} className="space-y-4" aria-busy={pending} aria-describedby={error ? "scout-join-error" : undefined}>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2"><label htmlFor="scout-join-first" className="text-sm font-medium">First name</label><Input id="scout-join-first" name="firstName" autoComplete="given-name" defaultValue={first ?? ""} required maxLength={50} disabled={!valid} className="h-11" /></div>
              <div className="space-y-2"><label htmlFor="scout-join-last" className="text-sm font-medium">Last name</label><Input id="scout-join-last" name="lastName" autoComplete="family-name" defaultValue={rest.join(" ")} required maxLength={50} disabled={!valid} className="h-11" /></div>
            </div>
            <div className="space-y-2"><label htmlFor="scout-join-email" className="text-sm font-medium">Email</label><Input id="scout-join-email" name="email" type="email" autoComplete="username" required maxLength={254} placeholder="you@company.com" disabled={!valid} className="h-11" /></div>
            <div className="space-y-2"><label htmlFor="scout-join-password" className="text-sm font-medium">Password</label><Input id="scout-join-password" name="password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} aria-describedby="scout-join-password-hint" disabled={!valid} className="h-11" /><p id="scout-join-password-hint" className="text-xs leading-5 text-muted-foreground">At least 12 characters, or your existing Scout password.</p></div>
            {error && <p id="scout-join-error" ref={errorRef} tabIndex={-1} role="alert" className="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-sm leading-5 text-danger">{error}</p>}
            <Button id="scout-join-submit" type="submit" disabled={pending || !valid} className="h-11 w-full">{pending ? "Please wait…" : `Join ${organization.name}`}</Button>
          </form>
          <div className="border-t pt-5 text-center text-sm text-muted-foreground">Already a member? <Link href={`/${organization.slug}`} className="font-medium text-foreground underline-offset-4 hover:underline">Sign in</Link></div>
        </CardContent>
      </Card>
    </div>
    <footer className="space-y-1.5 text-center text-xs leading-5 text-muted-foreground"><p>Scout · Your finance agent</p>{(legal?.privacy || legal?.terms) && <nav aria-label="Legal information" className="flex items-center justify-center gap-3">{legal?.privacy && <a href={legal.privacy} className="underline-offset-4 hover:text-foreground hover:underline">Privacy</a>}{legal?.terms && <a href={legal.terms} className="underline-offset-4 hover:text-foreground hover:underline">Terms</a>}</nav>}</footer>
  </main>;
}
