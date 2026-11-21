export const DEFAULT_STRATEGY_PROMPT = "Find current world news relevant to our company, explain likely implications and suggest next steps.";
export type StrategySettings = { provider: string | null; model: string | null; companyContext: string; systemPrompt: string; enabled: boolean; nextRunAt: string | null };
export function parseStrategyPreferences(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid strategy settings.");
  const row = value as Record<string, unknown>;
  if (typeof row.enabled !== "boolean") throw new Error("Choose whether daily research is enabled.");
  for (const [key, max] of [["companyContext", 4000], ["systemPrompt", 8000]] as const) {
    if (typeof row[key] !== "string" || !row[key].trim() || row[key].length > max) throw new Error(`Enter ${key === "companyContext" ? "company context (up to 4,000 characters)" : "a system prompt (up to 8,000 characters)"}.`);
  }
  return { companyContext: (row.companyContext as string).trim(), systemPrompt: (row.systemPrompt as string).trim(), enabled: row.enabled };
}
