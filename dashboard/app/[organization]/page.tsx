import { notFound } from "next/navigation";
import { OrganizationAuth } from "@/components/organization-auth";
import { Dashboard } from "@/components/dashboard";
import { authStore, currentSession, legalLinks } from "@/lib/auth";
import { getDashboardData } from "@/lib/finance";
import { validSlug } from "@/lib/auth-crypto";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const AUTH_ERRORS: Record<string, string> = { "failed": "Google sign-in could not be completed. Please try again.", "no-membership": "This Google account is not linked to a member of this organization. Sign in with your password first.", "google-unavailable": "Google sign-in is not configured yet.", "unavailable": "Sign-in is temporarily unavailable." };
export default async function OrganizationPage({ params, searchParams }: { params: Promise<{ organization: string }>; searchParams: Promise<{ auth?: string }> }) {
  const { organization: slug } = await params;
  if (!validSlug(slug)) notFound();
  const organization = await authStore().organization(slug);
  if (!organization) notFound();
  const session = await currentSession(slug);
  if (!session) return <OrganizationAuth legal={legalLinks()} organization={organization} googleAvailable={Boolean(process.env.SCOUT_GOOGLE_CLIENT_ID && process.env.SCOUT_GOOGLE_CLIENT_SECRET)} initialError={AUTH_ERRORS[(await searchParams).auth ?? ""]} />;
  const data = await getDashboardData(slug);
  const outcome = (await searchParams).auth;
  const notice = outcome === "linked" ? "Google sign-in is now linked to your account." : AUTH_ERRORS[outcome ?? ""];
  if (data.workspace && notice) data.workspace.authNotice = notice;
  return <Dashboard data={data} />;
}
