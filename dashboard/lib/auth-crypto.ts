import { createHash, randomBytes, scrypt, timingSafeEqual } from "node:crypto";

const RESERVED = new Set(["api", "admin", "healthz", "tasks", "inbox", "audit", "connections", "login", "logout", "register", "new", "auth", "scout-icon", "favicon"]);
export function validSlug(slug: string): boolean {
  return slug.length >= 3 && slug.length <= 63 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) && !RESERVED.has(slug);
}
export function registeredSchema(id: string, schema: string, legacy: boolean): boolean {
  if (id === "00000000-0000-4000-8000-000000000001") return legacy === true && schema === "public";
  return legacy === false && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(id) && schema === `tenant_${id.replaceAll("-", "")}`;
}
export const tokenHash = (token: string) => createHash("sha256").update(token).digest("hex");
export const newToken = () => randomBytes(32).toString("base64url");
const derive = (password: string, salt: string): Promise<Buffer> => new Promise((resolve, reject) => {
  scrypt(password, salt, 64, { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 }, (error, key) => error ? reject(error) : resolve(key));
});
export async function passwordHash(password: string): Promise<string> {
  if (password.length < 12 || Buffer.byteLength(password) > 1024) throw new Error("Use a password with 12–1024 characters.");
  const salt = randomBytes(16).toString("hex");
  return `scrypt$${salt}$${(await derive(password, salt)).toString("hex")}`;
}
export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  if (Buffer.byteLength(password) > 1024 || !/^scrypt\$[a-f0-9]{32}\$[a-f0-9]{128}$/.test(stored)) return false;
  const [, salt, key] = stored.split("$");
  return timingSafeEqual(await derive(password, salt), Buffer.from(key, "hex"));
}
export function sameOrigin(origin: string | null, configured: string): boolean {
  try { return origin !== null && new URL(origin).origin === origin && origin === new URL(configured).origin; } catch { return false; }
}
export function validateIdentity(claims: Record<string, unknown>, client: string, nonce: string) {
  if (claims.iss !== "https://accounts.google.com" && claims.iss !== "accounts.google.com") throw new Error("Invalid issuer");
  if (claims.aud !== client || claims.nonce !== nonce || claims.email_verified !== true || typeof claims.exp !== "number" || claims.exp <= Date.now() / 1000 || typeof claims.sub !== "string" || !claims.sub || typeof claims.email !== "string" || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(claims.email)) throw new Error("Invalid identity");
  return { subject: claims.sub, email: claims.email.toLowerCase() };
}
