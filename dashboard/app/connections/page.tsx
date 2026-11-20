import { redirect } from "next/navigation";

// The Google OAuth callback returns here (?google=connected); keep the query for the toast.
export default async function ConnectionsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const google = (await searchParams).google;
  redirect(typeof google === "string" ? `/?google=${encodeURIComponent(google)}#connections` : "/#connections");
}
