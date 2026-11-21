/** Credentials live only in the chat component's memory and in the current request. */
export const EXTERNAL_PROVIDERS = [
  { id: "openai", name: "OpenAI", endpoint: "https://api.openai.com/v1/chat/completions", model: "gpt-4.1-mini" },
  { id: "mistral", name: "Mistral", endpoint: "https://api.mistral.ai/v1/chat/completions", model: "mistral-small-latest" },
  { id: "groq", name: "Groq", endpoint: "https://api.groq.com/openai/v1/chat/completions", model: "llama-3.3-70b-versatile" },
  { id: "xai", name: "xAI", endpoint: "https://api.x.ai/v1/chat/completions", model: "grok-4.7" },
] as const;
export type ExternalConfig = { provider: typeof EXTERNAL_PROVIDERS[number]["id"]; model: string; apiKey: string };

export function parseExternalConfig(value: unknown): ExternalConfig {
  if (!value || typeof value !== "object") throw new Error("Set up an external provider first.");
  const row = value as Record<string, unknown>;
  if (!EXTERNAL_PROVIDERS.some(provider => provider.id === row.provider)) throw new Error("Select an available provider.");
  if (typeof row.model !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,149}$/.test(row.model.trim()))
    throw new Error("Enter a valid model ID (up to 150 characters).");
  if (typeof row.apiKey !== "string" || !/^[\x21-\x7e]{8,512}$/.test(row.apiKey.trim()))
    throw new Error("Enter an API key (8–512 characters, without spaces).");
  return { provider: row.provider as ExternalConfig["provider"], model: row.model.trim(), apiKey: row.apiKey.trim() };
}
