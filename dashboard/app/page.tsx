import { Dashboard } from "@/components/dashboard";
import { getDashboardData } from "@/lib/finance";
import { isDevelopmentPreview } from "@/lib/development-preview";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function Home() {
  return <>
    {isDevelopmentPreview() && <p id="lantern-preview-notice" role="status" className="border-b border-warning-border bg-warning-muted px-4 py-3 text-center text-sm text-warning">Development preview · Sample company data · Live model chat</p>}
    <Dashboard data={await getDashboardData()} />
  </>;
}
