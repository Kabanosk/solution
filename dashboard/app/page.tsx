import { Dashboard } from "@/components/dashboard";
import { getDashboardData } from "@/lib/finance";
import { isDevelopmentPreview } from "@/lib/development-preview";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function Home() {
  return <>
    {isDevelopmentPreview() && <p id="scout-preview-notice" role="status" className="fixed right-3 bottom-3 z-40 rounded-md border border-warning-border bg-warning-muted px-3 py-1.5 text-xs text-warning md:bottom-3">Development preview · Sample data only</p>}
    <Dashboard data={await getDashboardData()} />
  </>;
}
