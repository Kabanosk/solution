import { cookies } from "next/headers";
import { authStore, appOrigin, cookieOptions, currentSession, SESSION_COOKIE } from "@/lib/auth";
import { sameOrigin, validSlug } from "@/lib/auth-crypto";
import { clientAddress } from "@/lib/auth-client-address";
import { readAuthBody } from "@/lib/auth-request";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const privateHeaders = { "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex" };
const json = (body: unknown, status = 200) => Response.json(body, { status, headers: privateHeaders });

export async function POST(request: Request, { params }: { params: Promise<{ action: string }> }) {
  if (!sameOrigin(request.headers.get("origin"), appOrigin())) return json({ error: "Cross-site request refused." }, 403);
  if (!request.headers.get("content-type")?.startsWith("application/json")) return json({ error: "Expected JSON." }, 415);
  let body: Record<string, unknown>;
  try { body = await readAuthBody(request); }
  catch (error) { const large = error instanceof Error && error.message.includes("large"); return json({ error: large ? "Request too large." : "Invalid request." }, large ? 413 : 400); }
  const { action } = await params;
  const field = (key: string) => typeof body[key] === "string" ? body[key] as string : "";
  const store = authStore();
  try {
    if (action === "logout") {
      const token = (await cookies()).get(SESSION_COOKIE)?.value;
      if (token) await store.logout(token);
      (await cookies()).set(SESSION_COOKIE, "", cookieOptions(0));
      return json({ redirect: "/" });
    }
    if (action === "find") {
      if (!(await store.rateLimit(`organization-search:${clientAddress(request.headers)}`, 300))) return json({ error: "Too many requests. Try again later." }, 429);
      const found = await store.findOrganization(field("organization"));
      return found ? json(found) : json({ error: "Organization not found. Ask your team for its Scout URL." }, 404);
    }
    if (action !== "register" && action !== "login") return json({ error: "Not found." }, 404);
    const email = field("email").trim().toLowerCase(), slug = field("slug");
    if (!validSlug(slug)) return json({ error: "Invalid organization URL." }, 400);
    // Account-wide: varying a requested organization can never reset password-guess limits.
    if (!(await store.rateLimit(`${action}:address:${clientAddress(request.headers)}`, action === "register" ? 10 : 100)) || !(await store.rateLimit(`${action}:account:${email}`))) return json({ error: "Too many attempts. Try again in 15 minutes." }, 429);
    const result = action === "register" ? await store.register({ name: field("name"), slug, email, ownerName: field("ownerName"), password: field("password") }) : await store.login(slug, email, field("password"));
    if (!result) return json({ error: "Email, password or organization access is incorrect." }, 401);
    const previous = (await cookies()).get(SESSION_COOKIE)?.value;
    if (previous) await store.logout(previous);
    (await cookies()).set(SESSION_COOKIE, result.token, cookieOptions());
    return json({ redirect: `/${result.session.slug}` });
  } catch (error) {
    // Only validation messages cross the boundary, never SQL or connection diagnostics.
    const message = error instanceof Error && ["More than one organization uses this name. Enter the organization URL.", "Each account can own up to three organizations.", "Check your organization name, URL, name and email.", "Use your existing account password or a different email.", "That organization URL or account is already registered.", "Use a password with 12–1024 characters."].includes(error.message) ? error.message : "Authentication is temporarily unavailable.";
    return json({ error: message }, message === "Authentication is temporarily unavailable." ? 503 : 400);
  }
}
