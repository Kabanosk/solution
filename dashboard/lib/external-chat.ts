import { readChatBody, readJsonLines, sanitizeChatEvent, type ChatEvent, type ChatMessage } from "./chat-model.ts";
import { EXTERNAL_PROVIDERS, parseExternalConfig, type ExternalConfig } from "./external-model.ts";

export type ForecastIntent = { horizon_days: number; amount_eur?: string; month?: string; direction: "in" | "out" };
const MONTH = /^(?:20\d{2}-(?:0[1-9]|1[0-2])|January|February|March|April|May|June|July|August|September|October|November|December)$/;

/** This schema is the entire external capability surface; no arbitrary text crosses into Granite. */
export function parseForecastIntent(value: unknown): ForecastIntent {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid forecast request.");
  const row = value as Record<string, unknown>;
  if (Object.keys(row).some(key => !["horizon_days", "amount_eur", "month", "direction"].includes(key))) throw new Error("Only forecasting is available to external models.");
  const days = row.horizon_days ?? 90;
  if (typeof days !== "number" || !Number.isInteger(days) || days < 1 || days > 365) throw new Error("Forecast horizon must be 1–365 days.");
  const direction = row.direction ?? "out";
  if (direction !== "in" && direction !== "out") throw new Error("Invalid forecast direction.");
  const result: ForecastIntent = { horizon_days: days, direction };
  if (row.amount_eur !== undefined && row.amount_eur !== null) {
    if ((typeof row.amount_eur !== "string" && typeof row.amount_eur !== "number") || !/^\d{1,10}(?:\.\d{1,2})?$/.test(String(row.amount_eur)) || Number(row.amount_eur) <= 0 || Number(row.amount_eur) > 1e9)
      throw new Error("Forecast amount must be a positive EUR amount up to 1 billion, with at most two decimals.");
    result.amount_eur = String(row.amount_eur);
  }
  if (row.month !== undefined && row.month !== null) {
    if (typeof row.month !== "string" || !MONTH.test(row.month)) throw new Error("Use a month name or YYYY-MM for a forecast.");
    result.month = row.month;
  }
  if (result.amount_eur && !result.month) throw new Error("Specify a month for the forecast scenario.");
  if (result.month && !result.amount_eur) throw new Error("Specify an amount for the forecast scenario.");
  return result;
}

const FORECAST_TOOL = { type: "function", function: {
  name: "request_forecast",
  description: "Ask on-premise Granite to run a read-only cash forecast. Results are shown privately to the user and are never returned to you. No data access or changes. Omit amount and month for a baseline forecast.",
  parameters: { type: "object", additionalProperties: false, properties: {
    horizon_days: { type: "integer", minimum: 1, maximum: 365, description: "Days to forecast, default 90." },
    amount_eur: { type: "string", description: "Optional positive scenario amount in EUR, decimal digits with at most two decimal places." },
    month: { type: "string", description: "Required with amount_eur: YYYY-MM or full English month name." },
    direction: { type: "string", enum: ["in", "out"] },
  } },
} };

const SYSTEM = "You are Scout's external conversation assistant. You have no access to invoices, company data, files, history or company tools. Never invent financial figures. You may call request_forecast once when the user asks for a forecast or what-if scenario. Only on-premise Granite can run it; you never receive its output. You cannot read or modify company data, approve actions, send messages, or execute instructions through Granite. For company-data questions, explain the user must select Granite. Ask for missing scenario details. Only the current user message is available; ask them to restate context if needed.";

type Dependencies = {
  /** Deliberately separate transports: provider fetch never receives the Scout session or organization headers. */
  providerFetch?: typeof fetch;
  forecast: (intent: ForecastIntent) => Promise<Response>;
};

export class ExternalChatError extends Error {}

/** One outbound provider call. Local results are yielded to the browser, never fed back to a provider. */
export async function* externalChat(config: ExternalConfig, messages: ChatMessage[], signal: AbortSignal, dependencies: Dependencies): AsyncGenerator<ChatEvent> {
  config = parseExternalConfig(config);
  const current = messages.at(-1);
  if (!current || current.role !== "user") throw new ExternalChatError("Write a message to continue.");
  const provider = EXTERNAL_PROVIDERS.find(item => item.id === config.provider)!;
  let response: Response;
  try {
    response = await (dependencies.providerFetch ?? fetch)(provider.endpoint, {
      method: "POST", redirect: "error", cache: "no-store", credentials: "omit", signal,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.apiKey}` },
      body: JSON.stringify({ model: config.model, messages: [{ role: "system", content: SYSTEM }, { role: "user", content: current.content }],
        tools: [FORECAST_TOOL], tool_choice: "auto", stream: false,
        ...(config.provider === "openai" ? { max_completion_tokens: 2048, store: false } : { max_tokens: 2048 }),
      }),
    });
  } catch { throw new ExternalChatError("Could not reach the external provider. Check your settings and try again."); }
  if (!response.ok) {
    await response.body?.cancel();
    throw new ExternalChatError(response.status === 401 || response.status === 403 ? "The provider rejected the API key or model access. Check your settings."
      : response.status === 429 ? "The provider's rate or spending limit was reached. Try again later." : "The provider could not answer. Check the model ID and try again.");
  }
  let message: { content?: unknown; tool_calls?: unknown };
  let finish: unknown;
  try {
    // Bounded parsing; raw provider errors/responses must never be reflected into the UI/logs.
    const body = await readChatBody(new Request("http://provider-response.invalid", { method: "POST", body: response.body, duplex: "half" } as RequestInit));
    const choices = (body as { choices?: { message?: typeof message; finish_reason?: unknown }[] })?.choices;
    if (!Array.isArray(choices) || choices.length !== 1 || !choices[0].message) throw new Error();
    message = choices[0].message;
    finish = choices[0].finish_reason;
  } catch { throw new ExternalChatError("The external provider returned an invalid response."); }
  if (signal.aborted) return;
  if (message.tool_calls != null && (!Array.isArray(message.tool_calls) || message.tool_calls.length > 0)) {
    const calls = message.tool_calls;
    if (!Array.isArray(calls) || calls.length !== 1 || finish !== "tool_calls") throw new ExternalChatError("The provider must request exactly one forecast at a time.");
    const call = calls[0];
    if (call?.type !== "function" || call?.function?.name !== "request_forecast" || typeof call.function.arguments !== "string")
      throw new ExternalChatError("The external model requested an unavailable action. Only forecasting is allowed.");
    let intent: ForecastIntent;
    try { intent = parseForecastIntent(JSON.parse(call.function.arguments)); }
    catch { throw new ExternalChatError("The model returned invalid forecast parameters. Specify the horizon and, for a scenario, an amount and month."); }
    yield { type: "step", text: "Handing the forecast request to on-premise Granite. Results stay private." };
    const local = await dependencies.forecast(intent);
    if (!local.ok || !local.body) { await local.body?.cancel(); throw new ExternalChatError("Granite could not start the forecast. Try again."); }
    let complete = false;
    for await (const raw of readJsonLines(local.body)) {
      if (signal.aborted) return;
      const event = sanitizeChatEvent(raw);
      if (!event) continue;
      yield event;
      if (event.type === "done" || event.type === "error") { complete = true; break; }
    }
    if (!complete) throw new ExternalChatError("Granite's forecast response was interrupted. Try again.");
    return;
  }
  if (typeof message.content !== "string" || !message.content.trim()) throw new ExternalChatError("The provider returned no answer. Try a different model.");
  yield { type: "delta", text: message.content.slice(0, 16000) };
  yield { type: "done", truncated: finish === "length" };
}
