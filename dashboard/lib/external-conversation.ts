import { parseMarketResearchQuery, parsePublicResearch, type MarketResearchQuery, type PublicResearch } from "./market-research-model.ts";

export const MAX_EXTERNAL_CONTEXT_TOKEN = 270000;
export const MAX_EXTERNAL_CONTEXT_BYTES = 196608;
const MAX_TURNS = 40;
const MAX_TURN_CHARS = 8000;

export type PublicConversation = {
  turns: { role: "user" | "assistant"; content: string }[];
  research?: { query: MarketResearchQuery; result: PublicResearch };
};

/** Only this server-produced state may be sealed; arbitrary browser history is never restored. */
export function parsePublicConversation(value: unknown): PublicConversation {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid public conversation.");
  const row = value as Record<string, unknown>;
  if (Object.keys(row).some(key => !["turns", "research"].includes(key)) || !Array.isArray(row.turns)
    || row.turns.length < 2 || row.turns.length > MAX_TURNS || row.turns.length % 2 !== 0) throw new Error("Invalid public conversation.");
  const turns = row.turns.map((value, index): PublicConversation["turns"][number] => {
    if (!value || typeof value !== "object") throw new Error("Invalid public turn.");
    const turn = value as Record<string, unknown>;
    const role = index % 2 ? "assistant" : "user";
    if (Object.keys(turn).some(key => !["role", "content"].includes(key)) || turn.role !== role
      || typeof turn.content !== "string" || !turn.content.trim() || turn.content.length > MAX_TURN_CHARS) throw new Error("Invalid public turn.");
    return { role, content: turn.content };
  });
  const result: PublicConversation = { turns };
  if (row.research !== undefined) {
    if (!row.research || typeof row.research !== "object") throw new Error("Invalid retained research.");
    const evidence = row.research as Record<string, unknown>;
    if (Object.keys(evidence).some(key => !["query", "result"].includes(key))) throw new Error("Invalid retained research.");
    result.research = { query: parseMarketResearchQuery(evidence.query), result: parsePublicResearch(evidence.result) };
  }
  if (new TextEncoder().encode(JSON.stringify(result)).length > MAX_EXTERNAL_CONTEXT_BYTES) throw new Error("Public conversation is too large.");
  return result;
}

const bounded = (text: string) => text.length <= MAX_TURN_CHARS ? text
  : `${text.slice(0, 3900)}\n[Earlier public text omitted to fit context.]\n${text.slice(-3900)}`;

/** Retain whole public exchanges. Financial responses must never be passed as `assistant`. */
export function continuePublicConversation(previous: PublicConversation | undefined, user: string, assistant: string,
  research?: PublicConversation["research"]): PublicConversation {
  const result: PublicConversation = {
    turns: [...(previous?.turns ?? []), { role: "user" as const, content: bounded(user) }, { role: "assistant" as const, content: bounded(assistant) }].slice(-MAX_TURNS),
    ...(research ? { research } : {}),
  };
  while (result.turns.length > 2 && new TextEncoder().encode(JSON.stringify(result)).length > MAX_EXTERNAL_CONTEXT_BYTES)
    result.turns.splice(0, 2);
  return parsePublicConversation(result);
}
