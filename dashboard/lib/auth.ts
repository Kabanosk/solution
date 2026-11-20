import { createHash, timingSafeEqual } from "node:crypto";
import { isDevelopmentPreview } from "./development-preview.ts";

export function authorized(header: string | null): boolean {
  if (isDevelopmentPreview()) return true;
  const username = process.env.DASHBOARD_USERNAME;
  const password = process.env.DASHBOARD_PASSWORD;
  if (!username || !password || !header?.startsWith("Basic ")) return false;
  const supplied = Buffer.from(header.slice(6), "base64");
  const expected = Buffer.from(`${username}:${password}`);
  const hash = (value: Buffer) => createHash("sha256").update(value).digest();
  return timingSafeEqual(hash(supplied), hash(expected));
}
