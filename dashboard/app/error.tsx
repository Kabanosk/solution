"use client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
export default function Error({ reset }: { reset: () => void }) {
  return <main id="scout-error" className="mx-auto max-w-xl p-8"><Card id="scout-error-card"><CardHeader id="scout-error-heading"><CardTitle id="scout-error-title">Company data is unavailable</CardTitle></CardHeader><CardContent id="scout-error-content" className="space-y-4"><p id="scout-error-message" role="alert">The company records could not be loaded. Try again shortly.</p><Button id="scout-error-retry" onClick={reset}>Try again</Button></CardContent></Card></main>;
}
