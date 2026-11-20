import { Card, CardContent } from "@/components/ui/card";
export default function Loading() {
  return <main id="scout-loading" className="mx-auto max-w-xl p-8"><Card id="scout-loading-card"><CardContent id="scout-loading-message" className="pt-6" role="status">Loading company records…</CardContent></Card></main>;
}
