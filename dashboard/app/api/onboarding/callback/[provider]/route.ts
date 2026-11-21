import { cookies } from "next/headers";
import { apiUrl } from "@/lib/scout-api";
import { appOrigin, cookieOptions, currentSession, sessionToken } from "@/lib/auth";
import { completeProviderCallback, makeBackendFetch, parseProvider, parseProviderCookie, providerCookieName, providerCookiePath, type OAuthProvider } from "@/lib/onboarding-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const redirect = (location: string) => new Response(null, { status: 303, headers: { Location: location, "Cache-Control": "private, no-store" } });

// Browser navigation back from Google/Slack. Authority is the provider proof cookie plus the actual signed-in owner;
// the slug in the cookie is only a selector. The proof cookie is single-use: always cleared, success or not.
export async function GET(request: Request, { params }: { params: Promise<{ provider: string }> }) {
  const origin = appOrigin();
  const provider = parseProvider((await params).provider);
  if (provider !== "google" && provider !== "slack") return new Response(null, { status: 404, headers: { "Cache-Control": "private, no-store" } });
  const name = providerCookieName(provider as OAuthProvider), jar = await cookies();
  const cookieValue = jar.get(name)?.value;
  jar.set(name, "", { ...cookieOptions(0), path: providerCookiePath(provider as OAuthProvider) });
  try {
    const slug = parseProviderCookie(cookieValue)?.slug;
    const session = slug ? await currentSession(slug) : null;
    const token = await sessionToken();
    const backend = makeBackendFetch({ apiBase: apiUrl(), token: token ?? "", slug: slug ?? "", timeoutMs: 10000 });
    const { location } = await completeProviderCallback({ provider: provider as OAuthProvider, origin, url: new URL(request.url), cookieValue, session: session && token ? { slug: session.slug, role: session.role } : null, backend });
    return redirect(location);
  } catch {
    return redirect(`${origin}/`);
  }
}
