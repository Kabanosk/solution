import { parseExternalConfig, type ExternalConfig } from "./external-model.ts";

export const CHAT_MODELS = [{ id: "modelcar-granite-4-0-h-tiny", name: "Granite 4.0 H Tiny" }] as const;
export const MAX_MESSAGE_LENGTH = 16000;
export type ChatMessage = { role: "user" | "assistant"; content: string };
/** steps: Scout tool-call lines for an assistant turn; taskId: the agent task Scout recorded for it.
 *  kind "summary": Scout compacted the earlier conversation into `content`; `replaced` counts the messages it stands for. */
export type ChatTurn = ChatMessage & { id: string; status: "complete" | "pending" | "stopped" | "error"; steps?: string[]; taskId?: string; files?: ChatFile[];
  kind?: "summary"; replaced?: number; awaiting?: boolean; followups?: ChatFollowup[] };
/** Something Scout started in an answer (a what-if, an approval) reporting back later: a `followup` step on the task. */
export type ChatFollowup = { id: number; text: string };
/** A file Scout produced for the answer (e.g. an invoice list as CSV); data is base64. */
export type ChatFile = { name: string; mime: string; dataB64: string; sentToSlack: boolean };
export type ChatEvent =
  | { type: "step"; text: string }
  | { type: "delta"; text: string }
  | { type: "done"; truncated: boolean; task_id?: string; followups?: boolean }
  | { type: "error"; message: string }
  | { type: "file"; name: string; mime: string; data_b64: string; sent_to_slack: boolean }
  | { type: "compacted"; summary: string; replaced: number };

export const SUMMARY_PREFIX = "Summary of the earlier conversation:";
/** The backend accepts 64 messages; keep a little room. */
export const MAX_REQUEST_MESSAGES = 62;
export const MAX_CONVERSATION_LENGTH = 120000;

/** Files Scout may hand over, by MIME type; anything else is offered as a plain download. */
const FILE_MIMES = new Set(["text/csv", "text/plain", "application/json", "application/pdf",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]);
export const MAX_FILE_B64 = 4 * 1024 * 1024;

const TASK_ID = /^[A-Z]{2,5}-\d{1,9}$/;

/** Validates one NDJSON event from the Scout API before it is forwarded to the browser; unknown shapes are dropped. */
export function sanitizeChatEvent(value: unknown): ChatEvent | null {
  if (!value || typeof value !== "object") return null;
  const event = value as Record<string, unknown>;
  const text = typeof event.text === "string" ? event.text : null;
  switch (event.type) {
    case "step": return text?.trim() ? { type: "step", text: text.slice(0, 500) } : null;
    case "delta": return text !== null ? { type: "delta", text } : null;
    case "done": return typeof event.task_id === "string" && TASK_ID.test(event.task_id)
      ? { type: "done", truncated: event.truncated === true, task_id: event.task_id, ...(event.followups === true ? { followups: true } : {}) }
      : { type: "done", truncated: event.truncated === true };
    case "file": {
      const data = typeof event.data_b64 === "string" ? event.data_b64.replace(/\s+/g, "") : "";
      if (!data || data.length > MAX_FILE_B64 || !/^[A-Za-z0-9+/]+={0,2}$/.test(data)) return null;
      // A bare file name: no paths, control characters or leading dots.
      const name = (typeof event.name === "string" ? event.name : "").split(/[\\/]/).pop()!.replace(/[\u0000-\u001f\u007f"<>:|?*]/g, "").replace(/^\.+/, "").trim().slice(0, 120) || "scout-file";
      const mime = typeof event.mime === "string" && FILE_MIMES.has(event.mime) ? event.mime : "application/octet-stream";
      return { type: "file", name, mime, data_b64: data, sent_to_slack: event.sent_to_slack === true };
    }
    case "compacted": {
      const summary = typeof event.summary === "string" ? event.summary.trim().slice(0, 8000) : "";
      const replaced = event.replaced;
      if (!summary || typeof replaced !== "number" || !Number.isSafeInteger(replaced) || replaced < 1 || replaced > 64) return null;
      return { type: "compacted", summary, replaced };
    }
    case "error": return { type: "error", message: typeof event.message === "string" && event.message.trim() ? event.message.slice(0, 500) : "Scout could not answer. Try again." };
    default: return null;
  }
}

export class ChatInputError extends Error {}

const CONVERSATION_ID = /^[A-Za-z0-9_-]{8,64}$/;

/** One dashboard conversation = one Scout chat task; "New chat" starts a new id. */
export function newConversationId(): string { return crypto.randomUUID().replace(/-/g, ""); }

export function parseChatRequest(value: unknown): { model: string; messages: ChatMessage[]; conversationId?: string; external?: ExternalConfig } {
  if (!value || typeof value !== "object") throw new ChatInputError("Invalid chat request.");
  const row = value as Record<string, unknown>;
  if (row.model !== "external" && !CHAT_MODELS.some(model => model.id === row.model)) throw new ChatInputError("Select an available model.");
  let external: ExternalConfig | undefined;
  if (row.model === "external") {
    try { external = parseExternalConfig(row.external); }
    catch (error) { throw new ChatInputError((error as Error).message); }
  } else if (row.external !== undefined) throw new ChatInputError("External credentials are only accepted for external models.");
  if (!Array.isArray(row.messages) || !row.messages.length || row.messages.length > 64) throw new ChatInputError("Start a new chat to continue.");
  let length = 0;
  // A compacted conversation starts with Scout's summary of the earlier turns; user/assistant alternate after it.
  const first = row.messages[0] as Record<string, unknown> | null;
  const offset = first && first.role === "assistant" && typeof first.content === "string" && first.content.startsWith(SUMMARY_PREFIX) ? 1 : 0;
  const messages = row.messages.map((value: unknown, index: number): ChatMessage => {
    if (!value || typeof value !== "object") throw new ChatInputError("Invalid chat message.");
    const message = value as Record<string, unknown>;
    const expected = index < offset || (index - offset) % 2 === 1 ? "assistant" : "user";
    if (message.role !== expected || typeof message.content !== "string" || !message.content.trim()) throw new ChatInputError("Invalid conversation. Start a new chat.");
    if (message.content.length > MAX_MESSAGE_LENGTH) throw new ChatInputError("Keep each message under 16,000 characters.");
    length += message.content.length;
    return { role: expected, content: message.content };
  });
  if (length > MAX_CONVERSATION_LENGTH) throw new ChatInputError("This conversation is too long. Start a new chat.");
  if (messages.at(-1)?.role !== "user") throw new ChatInputError("Write a message to continue.");
  if (row.conversationId !== undefined && (typeof row.conversationId !== "string" || !CONVERSATION_ID.test(row.conversationId)))
    throw new ChatInputError("Invalid conversation. Start a new chat.");
  return { model: row.model as string, messages, ...(row.conversationId ? { conversationId: row.conversationId as string } : {}), ...(external ? { external } : {}) };
}

// Unanswered/failed exchanges remain visible but do not become invented model
// context. A stopped response is included only if actual content was received.
// `sources[i]` is the id of the turn message i came from, so a "compacted" answer can be mapped back.
export function requestFor(turns: ChatTurn[]): { messages: ChatMessage[]; sources: string[] } {
  const summary = turns[0]?.kind === "summary" ? turns[0] : null;
  const pairs: { messages: ChatMessage[]; sources: string[] }[] = [];
  for (let index = summary ? 1 : 0; index < turns.length; index += 2) {
    const user = turns[index];
    const answer = turns[index + 1];
    if (!answer) pairs.push({ messages: [{ role: "user", content: user.content }], sources: [user.id] });
    else if ((answer.status === "complete" || answer.status === "stopped") && answer.content.trim()) {
      const updates = (answer.followups ?? []).map(update => `\n\nUpdate: ${update.text}`).join("");
      pairs.push({ messages: [{ role: "user", content: user.content }, { role: "assistant", content: (answer.content + updates).slice(0, MAX_MESSAGE_LENGTH) }], sources: [user.id, answer.id] });
    }
  }
  // Fallback for very long chats the backend has not compacted: the newest whole exchanges that fit.
  const room = MAX_REQUEST_MESSAGES - (summary ? 1 : 0);
  let kept = pairs, length = 0;
  for (let index = pairs.length - 1, count = 0; index >= 0; index--) {
    count += pairs[index].messages.length;
    length += pairs[index].messages.reduce((sum, message) => sum + message.content.length, 0);
    if (count > room || length > MAX_CONVERSATION_LENGTH - 9000) { kept = pairs.slice(index + 1); break; }
  }
  const head = summary ? { messages: [{ role: "assistant" as const, content: `${SUMMARY_PREFIX}\n${summary.content}`.slice(0, MAX_MESSAGE_LENGTH) }], sources: [summary.id] } : null;
  const all = head ? [head, ...kept] : kept;
  return { messages: all.flatMap(pair => pair.messages), sources: all.flatMap(pair => pair.sources) };
}

export function conversationForRequest(turns: ChatTurn[]): ChatMessage[] {
  return requestFor(turns).messages;
}

/** Scout summarized the first `replaced` messages it was sent: put one summary item in their place.
 *  Turns before the last summarized one (including failed exchanges that were never sent) go with them. */
export function applyCompaction(turns: ChatTurn[], sources: string[], event: { summary: string; replaced: number }, id: string): ChatTurn[] {
  const covered = new Set(sources.slice(0, event.replaced));
  let last = -1;
  turns.forEach((turn, index) => { if (covered.has(turn.id)) last = index; });
  if (last < 0) return turns;
  const replaced = turns.slice(0, last + 1).filter(turn => covered.has(turn.id))
    .reduce((sum, turn) => sum + (turn.kind === "summary" ? turn.replaced ?? 1 : 1), 0);
  return [{ id, role: "assistant", kind: "summary", content: event.summary, status: "complete", replaced }, ...turns.slice(last + 1)];
}

export async function readChatBody(request: Request): Promise<unknown> {
  if (!request.body) throw new ChatInputError("Write a message to continue.");
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  let bytes = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 524288) { await reader.cancel(); throw new ChatInputError("This conversation is too long. Start a new chat."); }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    try { return JSON.parse(text); } catch { throw new ChatInputError("Invalid chat request."); }
  } finally { reader.releaseLock(); }
}

export async function* readJsonLines(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, end).trim(); buffer = buffer.slice(end + 1);
        if (line) yield JSON.parse(line) as ChatEvent;
      }
      if (done) break;
    }
    if (buffer.trim()) yield JSON.parse(buffer) as ChatEvent;
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

/** Attach the task's new `followup` steps to the answer that is waiting for them (the newest such answer).
 *  Returns the same array when there is nothing new, so React can skip the update. */
export function mergeFollowups(turns: ChatTurn[], steps: { id: number; kind: string; text: string }[]): ChatTurn[] {
  const known = new Set(turns.flatMap(turn => (turn.followups ?? []).map(update => update.id)));
  const fresh = steps.filter(step => step.kind === "followup" && !known.has(step.id) && step.text.trim())
    .sort((a, b) => a.id - b.id).map(step => ({ id: step.id, text: step.text.slice(0, MAX_MESSAGE_LENGTH) }));
  if (!fresh.length) return turns;
  let target = -1;
  for (let index = turns.length - 1; index >= 0; index--) if (turns[index].awaiting) { target = index; break; }
  if (target < 0) return turns;
  return turns.map((turn, index) => index === target ? { ...turn, followups: [...(turn.followups ?? []), ...fresh] } : turn);
}

/** The followups on screen that Scout has not been told about yet, per task: a colleague's reply is sent to the
 *  asker's Slack unless the open chat acknowledges having shown it. */
export function unacknowledgedFollowups(turns: ChatTurn[], acknowledged: ReadonlySet<number>): { taskId: string; stepIds: number[] }[] {
  const byTask = new Map<string, number[]>();
  for (const turn of turns) {
    if (!turn.taskId) continue;
    for (const update of turn.followups ?? []) {
      if (acknowledged.has(update.id)) continue;
      byTask.set(turn.taskId, [...(byTask.get(turn.taskId) ?? []), update.id]);
    }
  }
  return [...byTask].map(([taskId, stepIds]) => ({ taskId, stepIds: stepIds.slice(0, 50) }));
}
