import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { parseExternalConfig, type ExternalConfig } from "./external-model.ts";
function key() {
  const secret = process.env.SCOUT_STRATEGY_SECRET || process.env.API_KEY;
  if (!secret || secret.length < 32) throw new Error("Strategy credential storage is not configured. Ask an administrator to set SCOUT_STRATEGY_SECRET.");
  return createHash("sha256").update(secret).digest();
}
export function sealStrategyProvider(config: ExternalConfig, organizationId: string) {
  const iv = randomBytes(12), cipher = createCipheriv("aes-256-gcm", key(), iv);
  cipher.setAAD(Buffer.from(organizationId));
  const data = Buffer.concat([cipher.update(JSON.stringify(parseExternalConfig(config))), cipher.final()]);
  return [iv, cipher.getAuthTag(), data].map(part => part.toString("base64url")).join(".");
}
export function openStrategyProvider(value: string, organizationId: string): ExternalConfig {
  const [iv, tag, data] = value.split(".").map(part => Buffer.from(part, "base64url"));
  const cipher = createDecipheriv("aes-256-gcm", key(), iv);
  cipher.setAAD(Buffer.from(organizationId)); cipher.setAuthTag(tag);
  return parseExternalConfig(JSON.parse(Buffer.concat([cipher.update(data), cipher.final()]).toString("utf8")));
}
