import { Card, CardContent } from "@/components/ui/card";
export default function Loading() {
  return <main id="lantern-loading" className="mx-auto max-w-xl p-8"><Card id="lantern-loading-card"><CardContent id="lantern-loading-message" className="pt-6" role="status">Loading company records…</CardContent></Card></main>;
}
