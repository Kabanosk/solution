import { notFound } from "next/navigation";
import { JoinWorkspace } from "@/components/join-workspace";
import { authStore, legalLinks } from "@/lib/auth";
import { validSlug } from "@/lib/auth-crypto";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** The link Scout DMs on Slack: "Join <workspace> workspace". */
export default async function JoinPage({ params }: { params: Promise<{ organization: string; token: string }> }) {
  const { organization: slug, token } = await params;
  if (!validSlug(slug)) notFound();
  const store = authStore();
  const organization = await store.organization(slug);
  if (!organization) notFound();
  const invite = await store.invite(slug, token);
  return <JoinWorkspace organization={organization} token={token} valid={Boolean(invite)} slackName={invite?.slackName ?? ""} legal={legalLinks()} />;
}
