// Server-side only: do not import this cryptographic codec into client components.
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";
import { MAX_EXTERNAL_CONTEXT_BYTES, MAX_EXTERNAL_CONTEXT_TOKEN, parsePublicConversation, type PublicConversation } from "./external-conversation.ts";

const TTL_MS = 12 * 60 * 60 * 1000;
export type ExternalContextBinding = { sessionToken: string; organizationId: string; conversationId: string; provider: string; model: string; apiKey: string };
export class ExternalContextError extends Error {
  constructor() { super("The external chat context expired or is invalid. Start a new chat to continue."); }
}

const additionalData = (binding: ExternalContextBinding) => Buffer.from(JSON.stringify({
  session: createHash("sha256").update(binding.sessionToken).digest("hex"), organization: binding.organizationId,
  conversation: binding.conversationId, provider: binding.provider, model: binding.model,
  credential: createHash("sha256").update(binding.apiKey).digest("hex"),
}));

/** AEAD binds opaque public context to the authenticated session, tenant, tab conversation and model. */
export function createExternalContextCodec(key: Uint8Array) {
  if (key.length !== 32) throw new Error("Context encryption requires a 256-bit key.");
  return {
    seal(context: PublicConversation, binding: ExternalContextBinding, now = Date.now()): string {
      const state = parsePublicConversation(context);
      const iv = randomBytes(12);
      const cipher = createCipheriv("aes-256-gcm", key, iv);
      cipher.setAAD(additionalData(binding));
      const payload = Buffer.from(JSON.stringify({ expires: now + TTL_MS, state }));
      const encrypted = Buffer.concat([cipher.update(payload), cipher.final()]);
      return `v1.${iv.toString("base64url")}.${encrypted.toString("base64url")}.${cipher.getAuthTag().toString("base64url")}`;
    },
    open(token: string, binding: ExternalContextBinding, now = Date.now()): PublicConversation {
      try {
        if (typeof token !== "string" || token.length > MAX_EXTERNAL_CONTEXT_TOKEN) throw new Error();
        const parts = token.split(".");
        if (parts.length !== 4 || parts[0] !== "v1" || parts.slice(1).some(part => !/^[A-Za-z0-9_-]+$/.test(part))) throw new Error();
        const [iv, encrypted, tag] = parts.slice(1).map(part => Buffer.from(part, "base64url"));
        if (iv.length !== 12 || tag.length !== 16 || encrypted.length > MAX_EXTERNAL_CONTEXT_BYTES + 128) throw new Error();
        const decipher = createDecipheriv("aes-256-gcm", key, iv);
        decipher.setAAD(additionalData(binding));
        decipher.setAuthTag(tag);
        const payload = JSON.parse(Buffer.concat([decipher.update(encrypted), decipher.final()]).toString("utf8"));
        if (!Number.isSafeInteger(payload.expires) || payload.expires <= now || payload.expires > now + TTL_MS) throw new Error();
        return parsePublicConversation(payload.state);
      } catch { throw new ExternalContextError(); }
    },
  };
}

function serverKey(): Buffer {
  const configured = process.env.SCOUT_CHAT_CONTEXT_SECRET;
  if (configured) {
    if (configured.length < 32) throw new Error("SCOUT_CHAT_CONTEXT_SECRET must contain at least 32 characters.");
    return createHash("sha256").update(configured).digest();
  }
  // One-process deployments work without new configuration. Configure the same
  // server-only secret on all replicas to preserve context across restarts/scaling.
  const runtime = globalThis as typeof globalThis & { scoutExternalContextKey?: Buffer };
  return runtime.scoutExternalContextKey ??= randomBytes(32);
}

export const sealExternalContext = (context: PublicConversation, binding: ExternalContextBinding) => createExternalContextCodec(serverKey()).seal(context, binding);
export const openExternalContext = (token: string, binding: ExternalContextBinding) => createExternalContextCodec(serverKey()).open(token, binding);
