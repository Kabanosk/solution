// Characterization adapters only. original.ts is an unchanged source copy.
// The string interfaces let rust-anything record typed JSON and clock inputs.
import crypto from "node:crypto";
import { syncBuiltinESMExports } from "node:module";
import * as original from "./original.ts";

function entropy(value: string): Buffer {
  if (!/^[a-f0-9]{64}$/.test(value)) throw new Error("Invalid test entropy");
  return Buffer.from(value, "hex");
}

// Crypto randomness is controlled only in this isolated characterization copy.
// Production Rust uses the operating system RNG instead of these test bytes.
async function withEntropy<T>(value: string, operation: () => T | Promise<T>): Promise<T> {
  const bytes = entropy(value);
  const saved = crypto.randomBytes;
  crypto.randomBytes = ((size: number) => Buffer.from(bytes.subarray(0, size))) as typeof crypto.randomBytes;
  syncBuiltinESMExports();
  try { return await operation(); }
  finally { crypto.randomBytes = saved; syncBuiltinESMExports(); }
}

export function checkValidSlug(slug: string): boolean {
  return original.validSlug(slug);
}

export function checkRegisteredSchema(id: string, schema: string, legacy: boolean): boolean {
  return original.registeredSchema(id, schema, legacy);
}

export function checkTokenHash(token: string): string {
  return original.tokenHash(token);
}

export async function checkNewToken(testEntropy: string): Promise<string> {
  return withEntropy(testEntropy, () => original.newToken());
}

export async function checkPasswordHash(password: string, testEntropy: string): Promise<string> {
  return withEntropy(testEntropy, () => original.passwordHash(password));
}

export async function checkVerifyPassword(password: string, stored: string): Promise<boolean> {
  return original.verifyPassword(password, stored);
}

export function checkSameOrigin(originJson: string, configured: string): boolean {
  let origin: unknown;
  try { origin = JSON.parse(originJson); }
  catch { throw new Error("Invalid origin JSON"); }
  if (origin !== null && typeof origin !== "string") throw new Error("Invalid origin JSON");
  return original.sameOrigin(origin, configured);
}

export function checkValidateIdentity(claimsJson: string, client: string, nonce: string, now: number): string {
  let claims: Record<string, unknown>;
  try {
    const parsed = JSON.parse(claimsJson);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    claims = parsed;
  } catch { throw new Error("Invalid claims JSON"); }
  const saved = Date.now;
  Date.now = () => now;
  try { return JSON.stringify(original.validateIdentity(claims, client, nonce)); }
  finally { Date.now = saved; }
}
