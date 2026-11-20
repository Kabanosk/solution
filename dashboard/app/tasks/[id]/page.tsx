import { notFound } from "next/navigation";
import { Dashboard } from "@/components/dashboard";
import { getDashboardData, getTaskDetail } from "@/lib/finance";
import { isDevelopmentPreview } from "@/lib/development-preview";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [data, detail] = await Promise.all([getDashboardData(), getTaskDetail(decodeURIComponent(id))]);
  if (!detail) notFound();
  return <>
    {isDevelopmentPreview() && <p id="scout-preview-notice" role="status" className="fixed right-3 bottom-3 z-40 rounded-md border border-warning-border bg-warning-muted px-3 py-1.5 text-xs text-warning md:bottom-3">Development preview · Sample data only</p>}
    <Dashboard data={data} detail={detail} />
  </>;
}
