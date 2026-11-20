export const CHAT_MODELS = [{ id: "modelcar-granite-4-0-h-tiny", name: "Granite 4.0 H Tiny" }] as const;
export const MAX_MESSAGE_LENGTH = 16000;
export type ChatMessage = { role: "user" | "assistant"; content: string };
export type ChatTurn = ChatMessage & { id: string; status: "complete" | "pending" | "stopped" | "error" };
export type ChatEvent = { type: "delta"; text: string } | { type: "done"; truncated: boolean } | { type: "error"; message: string };

export class ChatInputError extends Error {}

export function parseChatRequest(value: unknown): { model: string; messages: ChatMessage[] } {
  if (!value || typeof value !== "object") throw new ChatInputError("Invalid chat request.");
  const row = value as Record<string, unknown>;
  if (!CHAT_MODELS.some(model => model.id === row.model)) throw new ChatInputError("Select an available model.");
  if (!Array.isArray(row.messages) || !row.messages.length || row.messages.length > 64) throw new ChatInputError("Start a new chat to continue.");
  let length = 0;
  const messages = row.messages.map((value: unknown, index: number): ChatMessage => {
    if (!value || typeof value !== "object") throw new ChatInputError("Invalid chat message.");
    const message = value as Record<string, unknown>;
    const expected = index % 2 === 0 ? "user" : "assistant";
    if (message.role !== expected || typeof message.content !== "string" || !message.content.trim()) throw new ChatInputError("Invalid conversation. Start a new chat.");
    if (message.content.length > MAX_MESSAGE_LENGTH) throw new ChatInputError("Keep each message under 16,000 characters.");
    length += message.content.length;
    return { role: expected, content: message.content };
  });
  if (length > 64000) throw new ChatInputError("This conversation is too long. Start a new chat.");
  if (messages.at(-1)?.role !== "user") throw new ChatInputError("Write a message to continue.");
  return { model: row.model as string, messages };
}

// Unanswered/failed exchanges remain visible but do not become invented model
// context. A stopped response is included only if actual content was received.
export function conversationForRequest(turns: ChatTurn[]): ChatMessage[] {
  const messages: ChatMessage[] = [];
  for (let index = 0; index < turns.length; index += 2) {
    const user = turns[index];
    const answer = turns[index + 1];
    if (!answer) messages.push({ role: "user", content: user.content });
    else if ((answer.status === "complete" || answer.status === "stopped") && answer.content.trim()) {
      messages.push({ role: "user", content: user.content }, { role: "assistant", content: answer.content });
    }
  }
  return messages;
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
      if (bytes > 262144) { await reader.cancel(); throw new ChatInputError("This conversation is too long. Start a new chat."); }
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
