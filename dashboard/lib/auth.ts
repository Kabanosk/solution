import { createHash, timingSafeEqual } from "node:crypto";

export function authorized(header: string | null): boolean {
  const username = process.env.DASHBOARD_USERNAME;
  const password = process.env.DASHBOARD_PASSWORD;
  if (!username || !password || !header?.startsWith("Basic ")) return false;
  const supplied = Buffer.from(header.slice(6), "base64");
  const expected = Buffer.from(`${username}:${password}`);
  const hash = (value: Buffer) => createHash("sha256").update(value).digest();
  return timingSafeEqual(hash(supplied), hash(expected));
}
