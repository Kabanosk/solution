import { notFound } from "next/navigation";
import { LoginLink } from "@/components/login-link";
import { authStore } from "@/lib/auth";
import { validSlug } from "@/lib/auth-crypto";
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** The one-click sign-in link Scout sends on Slack. The token is spent by a POST from the page, never by the GET
 *  itself, so link previews and scanners cannot use it up. */
export default async function LoginLinkPage({ params }: { params: Promise<{ organization: string; token: string }> }) {
  const { organization: slug, token } = await params;
  if (!validSlug(slug)) notFound();
  const organization = await authStore().organization(slug);
  if (!organization) notFound();
  return <LoginLink organization={organization} token={token} />;
}
