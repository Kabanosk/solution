"use client";

import { useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { ScoutMark } from "@/components/brand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ThemeToggle } from "@/components/theme-toggle";
import { DomIds } from "@/components/dom-ids";

function GoogleMark() {
  // Decorative official artwork: the adjacent button text supplies the accessible name.
  // eslint-disable-next-line @next/next/no-img-element
  return <img src="/google-g.png" alt="" aria-hidden="true" width={18} height={18} className="size-[18px] shrink-0 object-contain" />;
}

type Organization = { name: string; slug: string };
export function OrganizationAuth({ organization, googleAvailable = false, initialCreating = false, initialError, legal }: { organization?: Organization; googleAvailable?: boolean; initialCreating?: boolean; initialError?: string; legal?: { privacy?: string; terms?: string } }) {
  const [creating, setCreating] = useState(initialCreating);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(initialError ?? "");
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const mode = organization ? "login" : creating ? "register" : "find";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setPending(true); setError("");
    const values = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const response = await fetch(`/api/auth/${mode}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...values, ...(organization ? { slug: organization.slug } : {}) }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error ?? "Please try again.");
      // Full navigation resets all in-memory company data, conversations and pending fetches.
      window.location.assign(mode === "find" ? `/${body.slug}` : body.redirect);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not connect. Please try again."); requestAnimationFrame(() => errorRef.current?.focus()); }
    finally { setPending(false); }
  }
  async function createWithGoogle() {
    if (!googleAvailable || pending || !formRef.current) return;
    const form = formRef.current;
    // Validate only workspace details; Google supplies the verified owner identity.
    for (const name of ["name", "slug"]) {
      const input = form.elements.namedItem(name) as HTMLInputElement | null;
      if (!input?.reportValidity()) return;
    }
    const values = new FormData(form);
    setPending(true); setError("");
    try {
      const response = await fetch("/api/auth/google/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ intent: "register", name: values.get("name"), slug: values.get("slug") }) });
      const body = await response.json();
      if (!response.ok || typeof body.url !== "string" || !body.url) throw new Error(body.error ?? "Could not start Google sign-up. Please try again.");
      window.location.assign(body.url);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not connect. Please try again."); setPending(false); requestAnimationFrame(() => errorRef.current?.focus()); }
  }
  const changeMode = () => { setCreating(value => !value); setError(""); };
  return <main id="scout-auth-page" className="flex min-h-dvh flex-col bg-background px-5 py-6 text-foreground sm:px-8">
    <div id="scout-auth-center" className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center py-12">
      <header id="scout-auth-header" className="mb-8 flex items-center justify-between gap-4">
        <Link id="scout-auth-brand" href="/" className="flex items-center gap-2.5 text-base font-semibold tracking-tight" aria-label="Scout home"><ScoutMark size={28} />Scout</Link>
        <ThemeToggle />
      </header>
      <Card id="scout-auth-card" className="gap-6 border-0 bg-transparent py-0 shadow-none">
        <CardHeader id="scout-auth-card-header" className="gap-3 px-0">
          {organization && <p id="scout-auth-org" className="text-sm font-medium text-muted-foreground">{organization.name}</p>}
          <CardTitle id="scout-auth-title" className="text-2xl font-semibold tracking-tight"><h1 id="scout-auth-heading">{organization ? "Welcome back" : creating ? "Create your organization" : "Your organization, in focus."}</h1></CardTitle>
          <CardDescription id="scout-auth-description" className="text-sm leading-6">{organization ? "Sign in to your organization’s private Scout workspace." : creating ? "A private workspace for your team and your finance agent." : "Find your team’s workspace or start a new one. Your data stays with your organization."}</CardDescription>
        </CardHeader>
        <CardContent id="scout-auth-content" className="space-y-5 px-0">
          <form id="scout-auth-form" ref={formRef} onSubmit={submit} className="space-y-4" aria-busy={pending} aria-describedby={error ? "scout-auth-error" : undefined}>
            {mode === "find" ? <div id="scout-find-field" className="space-y-2"><label id="scout-find-label" htmlFor="scout-find-input" className="text-sm font-medium">Organization name or URL</label><Input id="scout-find-input" name="organization" autoComplete="organization" placeholder="e.g. Pizzeria Amalfi" required maxLength={100} className="h-11" /><p id="scout-find-hint" className="text-xs leading-5 text-muted-foreground">You’ll sign in on your organization’s own page.</p></div> : <>
              {creating && <>
                <div id="scout-name-field" className="space-y-2"><label id="scout-name-label" htmlFor="scout-name-input" className="text-sm font-medium">Organization name</label><Input id="scout-name-input" name="name" autoComplete="organization" required maxLength={100} placeholder="Your company" className="h-11" onChange={event => { if (!slugEdited) setSlug(event.target.value.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 63)); }} /></div>
                <div id="scout-slug-field" className="space-y-2"><label id="scout-slug-label" htmlFor="scout-slug-input" className="text-sm font-medium">Organization URL</label><Input id="scout-slug-input" name="slug" value={slug} onChange={event => { setSlugEdited(true); setSlug(event.target.value); }} autoComplete="off" required minLength={3} maxLength={63} pattern="[a-z0-9]+(-[a-z0-9]+)*" aria-describedby="scout-slug-hint" placeholder="your-company" className="h-11 font-mono" /><p id="scout-slug-hint" className="text-xs leading-5 text-muted-foreground">/{slug || "your-company"} · Lowercase letters, numbers and single hyphens.</p></div>
                <div id="scout-google-signup-section" className="space-y-3">
                  <Button id="scout-google-signup" type="button" variant="outline" onClick={createWithGoogle} disabled={pending || !googleAvailable} aria-describedby={googleAvailable ? "scout-google-signup-hint" : "scout-google-signup-unavailable"} className="h-11 w-full"><GoogleMark />{pending ? "Please wait…" : "Create with Google"}</Button>
                  {googleAvailable ? <p id="scout-google-signup-hint" className="text-xs leading-5 text-muted-foreground">Use your Google name and email. No password needed.</p> : <p id="scout-google-signup-unavailable" className="text-xs leading-5 text-muted-foreground">Google sign-up hasn’t been configured by your administrator yet.</p>}
                  <div id="scout-signup-divider" className="flex items-center gap-3 text-xs text-muted-foreground"><span id="scout-signup-line-left" className="h-px flex-1 bg-border" />or use email<span id="scout-signup-line-right" className="h-px flex-1 bg-border" /></div>
                </div>
                <div id="scout-owner-field" className="space-y-2"><label id="scout-owner-label" htmlFor="scout-owner-input" className="text-sm font-medium">Your name</label><Input id="scout-owner-input" name="ownerName" autoComplete="name" required maxLength={100} className="h-11" /></div>
              </>}
              <div id="scout-email-field" className="space-y-2"><label id="scout-email-label" htmlFor="scout-email-input" className="text-sm font-medium">Email</label><Input id="scout-email-input" name="email" type="email" autoComplete="username" required maxLength={254} placeholder="you@company.com" className="h-11" /></div>
              <div id="scout-password-field" className="space-y-2"><label id="scout-password-label" htmlFor="scout-password-input" className="text-sm font-medium">Password</label><Input id="scout-password-input" name="password" type="password" autoComplete={creating ? "new-password" : "current-password"} required minLength={creating ? 12 : undefined} maxLength={1024} aria-describedby={creating ? "scout-password-hint" : undefined} className="h-11" />{creating && <p id="scout-password-hint" className="text-xs leading-5 text-muted-foreground">At least 12 characters. Password managers and paste are welcome.</p>}</div>
            </>}
            {error && <p id="scout-auth-error" ref={errorRef} tabIndex={-1} role="alert" className="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-sm leading-5 text-danger">{error}</p>}
            <Button id="scout-auth-submit" type="submit" disabled={pending} className="h-11 w-full">{pending ? "Please wait…" : mode === "find" ? "Continue" : creating ? "Create organization" : "Sign in"}</Button>
          </form>
          {organization && <div id="scout-google-section" className="space-y-3">
            <div id="scout-google-divider" className="flex items-center gap-3 text-xs text-muted-foreground"><span id="scout-google-line-left" className="h-px flex-1 bg-border" />or<span id="scout-google-line-right" className="h-px flex-1 bg-border" /></div>
            {googleAvailable ? <Button id="scout-google-login" asChild variant="outline" className="h-11 w-full"><a href={`/api/auth/google/start?organization=${encodeURIComponent(organization.slug)}`}><GoogleMark />Continue with Google</a></Button> : <><Button id="scout-google-login" variant="outline" disabled className="h-11 w-full" aria-describedby="scout-google-unavailable"><GoogleMark />Continue with Google</Button><p id="scout-google-unavailable" className="text-xs leading-5 text-muted-foreground">Google sign-in hasn’t been configured by your administrator yet.</p></>}
            <p id="scout-membership-hint" className="text-xs leading-5 text-muted-foreground">Access is for existing members only. Signing in with Google does not join this organization. First sign in with your password to link Google securely.</p>
          </div>}
          <div id="scout-auth-bottom" className="border-t pt-5 text-center text-sm text-muted-foreground">{organization ? <Link id="scout-other-org" href="/" className="text-foreground underline-offset-4 hover:underline">Find another organization</Link> : <><span id="scout-mode-prompt">{creating ? "Already have a workspace? " : "New to Scout? "}</span><button id="scout-mode-toggle" type="button" onClick={changeMode} disabled={pending} className="min-h-11 cursor-pointer font-medium text-foreground underline-offset-4 hover:underline focus-visible:rounded focus-visible:outline-2 focus-visible:outline-ring">{creating ? "Find your organization" : "Create organization"}</button></>}</div>
        </CardContent>
      </Card>
    </div>
    <footer id="scout-auth-footer" className="space-y-1.5 text-center text-xs leading-5 text-muted-foreground"><p id="scout-auth-footer-brand">Scout · Your finance agent</p><nav id="scout-auth-legal" aria-label="Legal information" className="flex items-center justify-center gap-3">{legal?.privacy ? <a id="scout-auth-privacy" href={legal.privacy} className="underline-offset-4 hover:text-foreground hover:underline">Privacy</a> : <span id="scout-auth-privacy-pending" title="Awaiting publication by the legal team">Privacy · coming soon</span>}{legal?.terms ? <a id="scout-auth-terms" href={legal.terms} className="underline-offset-4 hover:text-foreground hover:underline">Terms</a> : <span id="scout-auth-terms-pending" title="Awaiting publication by the legal team">Terms · coming soon</span>}</nav></footer><DomIds />
  </main>;
}
