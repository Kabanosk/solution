import { Pool } from "pg";
import { cookies, headers } from "next/headers";
import { AuthStore, type Session } from "./auth-store";

export const SESSION_COOKIE = "scout_session";
export const OAUTH_COOKIE = "scout_oauth";
let pool: Pool | undefined;
export function authStore() {
  pool ??= new Pool({ connectionString: process.env.SCOUT_AUTH_DATABASE_URL ?? process.env.DATABASE_URL,
    host: process.env.PGHOST, port: Number(process.env.PGPORT ?? 5432), database: process.env.PGDATABASE,
    user: process.env.PGUSER, password: process.env.PGPASSWORD, max: 5, connectionTimeoutMillis: 5000, statement_timeout: 15000 });
  return new AuthStore(pool);
}
export function appOrigin() {
  const configured = process.env.SCOUT_APP_ORIGIN;
  if (!configured) throw new Error("SCOUT_APP_ORIGIN is required");
  const url = new URL(configured);
  if (url.origin !== configured || (url.protocol !== "https:" && !(process.env.NODE_ENV !== "production" && url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname)))) throw new Error("Invalid SCOUT_APP_ORIGIN");
  return url.origin;
}
export function legalLinks() {
  const published = (value: string | undefined) => { try { return value && new URL(value).protocol === "https:" ? value : undefined; } catch { return undefined; } };
  return { privacy: published(process.env.SCOUT_PRIVACY_URL), terms: published(process.env.SCOUT_TERMS_URL) };
}
export function cookieOptions(maxAge = 43200) {
  return { httpOnly: true, secure: new URL(appOrigin()).protocol === "https:", sameSite: "lax" as const, path: "/", maxAge };
}
export async function currentSession(slug?: string): Promise<Session | null> {
  const org = slug ?? (await headers()).get("x-scout-organization") ?? undefined;
  return authStore().session((await cookies()).get(SESSION_COOKIE)?.value, org);
}
export async function requireSession(slug?: string): Promise<Session> {
  const session = await currentSession(slug);
  if (!session) throw new Error("Access denied");
  return session;
}
export async function sessionToken() { return (await cookies()).get(SESSION_COOKIE)?.value; }
