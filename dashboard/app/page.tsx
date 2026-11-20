import { Dashboard } from "@/components/dashboard";
import { getDashboardData } from "@/lib/finance";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function Home() {
  return <Dashboard data={await getDashboardData()} />;
}
