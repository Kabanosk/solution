"use client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
export default function Error({ reset }: { reset: () => void }) {
  return <main id="lantern-error" className="mx-auto max-w-xl p-8"><Card id="lantern-error-card"><CardHeader id="lantern-error-heading"><CardTitle id="lantern-error-title">Company data is unavailable</CardTitle></CardHeader><CardContent id="lantern-error-content" className="space-y-4"><p id="lantern-error-message" role="alert">The company records could not be loaded. Try again shortly.</p><Button id="lantern-error-retry" onClick={reset}>Try again</Button></CardContent></Card></main>;
}
