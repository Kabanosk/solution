"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ScoutMark } from "@/components/brand";
import { Button } from "@/components/ui/button";

export function LoginLink({ organization, token }: { organization: { name: string; slug: string }; token: string }) {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const started = useRef(false);
  async function signIn() {
    setPending(true); setError("");
    try {
      const response = await fetch("/api/auth/link", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ slug: organization.slug, token }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error ?? "Please try again.");
      window.location.assign(body.redirect);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not connect. Please try again."); setPending(false); }
  }
  useEffect(() => { if (!started.current) { started.current = true; void signIn(); } }); // one click in Slack is enough
  return <main id="scout-login-link-page" className="flex min-h-dvh items-center justify-center bg-background px-5 text-foreground">
    <div className="w-full max-w-sm space-y-6 text-center">
      <div className="flex items-center justify-center gap-2.5 text-base font-semibold tracking-tight"><ScoutMark size={28} />Scout</div>
      <h1 className="text-2xl font-semibold tracking-tight">Signing in to {organization.name}</h1>
      {error ? <>
        <p role="alert" className="rounded-md border border-danger/25 bg-danger/5 px-3 py-2 text-sm leading-5 text-danger">{error}</p>
        <Button asChild variant="outline" className="h-11 w-full"><Link href={`/${organization.slug}`}>Sign in with your password</Link></Button>
      </> : <>
        <p className="text-sm text-muted-foreground">{pending ? "One moment…" : "Continue to your workspace."}</p>
        <Button id="scout-login-link-continue" onClick={() => void signIn()} disabled={pending} className="h-11 w-full">{pending ? "Signing in…" : `Open ${organization.name}`}</Button>
      </>}
    </div>
  </main>;
}
