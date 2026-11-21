import { OrganizationAuth } from "@/components/organization-auth";
import { legalLinks } from "@/lib/auth";
export const dynamic = "force-dynamic";
export default async function Home({ searchParams }: { searchParams: Promise<{ create?: string | string[]; auth?: string | string[] }> }) {
  const query = await searchParams;
  const signupFailed = query.auth === "signup-failed";
  return <OrganizationAuth legal={legalLinks()} googleAvailable={Boolean(process.env.SCOUT_GOOGLE_CLIENT_ID && process.env.SCOUT_GOOGLE_CLIENT_SECRET)} initialCreating={query.create === "1" || signupFailed} initialError={signupFailed ? "Google sign-up could not be completed. If you have an existing account, sign in and link Google first, or choose another organization URL." : undefined} />;
}
