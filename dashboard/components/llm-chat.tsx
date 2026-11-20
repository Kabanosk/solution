"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Check, ChevronDown, Copy, LoaderCircle, MessageSquare, RotateCcw, Square, SquarePen } from "lucide-react";
import { CHAT_MODELS, MAX_MESSAGE_LENGTH, conversationForRequest, readJsonLines, type ChatTurn } from "@/lib/chat-model";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";

export function LlmChat({ active }: { active: boolean }) {
  const [model, setModel] = useState<string>(CHAT_MODELS[0].id);
  const [messages, setMessages] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const [truncated, setTruncated] = useState(false);
  const request = useRef<AbortController | null>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const follow = useRef(true);

  const scrollToEnd = () => { if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight; follow.current = true; setFollowing(true); };
  useEffect(() => { if (active && follow.current) scrollToEnd(); }, [messages, active]);
  useEffect(() => {
    if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 180)}px`; }
  }, [draft]);
  useEffect(() => () => { request.current?.abort(); }, []);

  async function reply(history: ChatTurn[]) {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const id = crypto.randomUUID();
    setMessages([...history, { id, role: "assistant", content: "", status: "pending" }]);
    setBusy(true); setError(null); setNotice(""); setTruncated(false);
    follow.current = true;
    const update = (change: Partial<ChatTurn>) => setMessages(current => current.map(turn => turn.id === id ? { ...turn, ...change } : turn));
    let content = "";
    let completed = false;
    let failure = "Could not reach the model. Try again.";
    try {
      const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, messages: conversationForRequest(history) }), signal: controller.signal });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        if (typeof body?.error === "string") failure = body.error;
        throw new Error("Chat request failed");
      }
      if (!response.body) throw new Error("No response stream");
      failure = "The response was interrupted. Try again.";
      for await (const event of readJsonLines(response.body)) {
        if (controller.signal.aborted) break;
        if (event.type === "delta" && typeof event.text === "string") { content += event.text; update({ content }); }
        else if (event.type === "error") { failure = event.message; throw new Error("Model stream failed"); }
        else if (event.type === "done") { completed = true; setTruncated(event.truncated); }
      }
      if (request.current !== controller) return;
      if (controller.signal.aborted) update({ status: "stopped" });
      else if (!completed || !content.trim()) throw new Error("Incomplete response");
      else { update({ status: "complete" }); setNotice("Response received."); }
    } catch {
      if (request.current !== controller) return;
      if (controller.signal.aborted) { update({ status: "stopped" }); setNotice("Response stopped."); }
      else { update({ status: "error" }); setError(failure); }
    } finally {
      if (request.current === controller) { request.current = null; setBusy(false); }
    }
  }

  function send() {
    const content = draft.trim();
    if (!content || busy || content.length > MAX_MESSAGE_LENGTH) return;
    const history: ChatTurn[] = [...messages, { id: crypto.randomUUID(), role: "user", content, status: "complete" }];
    setDraft("");
    void reply(history);
    input.current?.focus({ preventScroll: true });
  }

  function newChat() {
    request.current?.abort(); request.current = null;
    setMessages([]); setDraft(""); setBusy(false); setError(null); setNotice(""); setCopied(null); setTruncated(false);
    follow.current = true; setFollowing(true);
    input.current?.focus({ preventScroll: true });
  }

  async function copy(turn: ChatTurn) {
    try { await navigator.clipboard.writeText(turn.content); setCopied(turn.id); setNotice("Response copied."); }
    catch { setNotice("Could not copy the response."); }
  }

  const last = messages.at(-1);
  const retryable = last?.role === "assistant" && ["error", "stopped"].includes(last.status);

  return <section id="lantern-llm-chat" aria-labelledby="lantern-chat-title" className="flex h-[calc(100dvh-17rem)] min-h-[320px] min-w-0 flex-col md:h-[calc(100dvh-12rem)]">
    <h1 id="lantern-chat-title" className="sr-only">Chat</h1>
    <Button id="lantern-chat-new" variant="ghost" size="sm" onClick={newChat} className="mb-3 min-h-11 shrink-0 self-start text-muted-foreground"><SquarePen className="size-4" aria-hidden="true" /><span>New chat</span></Button>

    <div id="lantern-chat-conversation" ref={viewport} role="region" aria-label="Conversation" tabIndex={0} className="min-h-0 flex-1 overflow-y-auto overscroll-contain outline-none focus-visible:ring-2 focus-visible:ring-ring" onScroll={() => {
      const element = viewport.current;
      if (element) { follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; setFollowing(follow.current); }
    }}>
      {messages.length === 0 ? <div id="lantern-chat-empty" className="flex h-full min-h-44 flex-col items-center justify-center gap-4 px-3 pb-6 text-center">
        <span className="flex size-10 items-center justify-center rounded-xl border bg-card"><MessageSquare className="size-4 text-muted-foreground" aria-hidden="true" /></span>
        <div className="space-y-2"><h2 className="text-xl font-medium tracking-tight">Start a conversation</h2><p className="text-sm text-muted-foreground">Ask a question. Work through an idea.</p></div>
      </div> : <ol id="lantern-chat-messages" className="mx-auto max-w-3xl space-y-8 px-2 py-7 sm:px-6">
        {messages.map(turn => <li key={turn.id} id={`lantern-chat-message-${turn.id}`} className="min-w-0 space-y-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="font-medium text-foreground">{turn.role === "user" ? "You" : "Assistant"}</span>{turn.status === "stopped" && <span>Stopped</span>}{turn.status === "error" && <span className="text-danger">Interrupted</span>}</div>
          <div className="min-w-0 whitespace-pre-wrap break-words text-sm leading-7 [overflow-wrap:anywhere]">{turn.content || (turn.status === "pending" ? <span className="inline-flex items-center gap-2 text-muted-foreground"><LoaderCircle aria-hidden="true" className="size-3.5 motion-safe:animate-spin" />Responding…</span> : <span className="text-muted-foreground">No response received.</span>)}</div>
          {turn.role === "assistant" && turn.content && turn.status !== "pending" && <Button variant="ghost" size="sm" aria-label="Copy response" className="min-h-11 min-w-11 px-2 text-muted-foreground" onClick={() => void copy(turn)}>{copied === turn.id ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}</Button>}
        </li>)}
      </ol>}
    </div>

    <div className="mx-auto w-full max-w-3xl shrink-0 space-y-2 pt-3 sm:px-6">
      {!following && messages.length > 0 && <div className="flex justify-center"><Button id="lantern-chat-latest" variant="outline" size="sm" className="min-h-11 gap-2 rounded-full" onClick={scrollToEnd}><ArrowDown aria-hidden="true" className="size-3.5" />Latest</Button></div>}
      {error && <p id="lantern-chat-error" role="alert" className="text-sm leading-6 text-danger">{error}</p>}
      {!busy && retryable && <Button id="lantern-chat-retry" variant="ghost" size="sm" className="min-h-11 text-muted-foreground" onClick={() => void reply(messages.slice(0, -1))}><RotateCcw aria-hidden="true" className="size-3.5" />Try again</Button>}
      {truncated && <p role="status" className="text-xs text-muted-foreground">Response reached its length limit. Send a follow-up to continue.</p>}
      <form id="lantern-chat-composer" className="rounded-xl border bg-card p-2 shadow-xs focus-within:border-ring" onSubmit={event => { event.preventDefault(); send(); }}>
        <label className="sr-only" htmlFor="lantern-chat-input">Message the assistant</label>
        <Textarea id="lantern-chat-input" ref={input} rows={2} value={draft} maxLength={MAX_MESSAGE_LENGTH} placeholder="Write a message…" className="min-h-14 resize-none border-0 bg-transparent px-2 py-2 shadow-none focus-visible:ring-0" onChange={event => setDraft(event.target.value)} onKeyDown={event => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
        }} />
        <div className="flex min-w-0 items-center justify-between gap-3">
          <DropdownMenu><DropdownMenuTrigger asChild><Button id="lantern-chat-model" type="button" variant="ghost" size="sm" aria-label="Select model" className="h-11 min-w-0 max-w-[calc(100%-3.5rem)] gap-1.5 px-2 text-xs font-normal text-muted-foreground md:h-8" disabled={busy}><span className="truncate">{CHAT_MODELS.find(item => item.id === model)?.name ?? model}</span><ChevronDown aria-hidden="true" className="size-3" /></Button></DropdownMenuTrigger>
            <DropdownMenuContent id="lantern-chat-model-menu" side="top" align="start" className="max-w-[calc(100vw-2rem)]">
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Available models</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={model} onValueChange={setModel}>{CHAT_MODELS.map(item => <DropdownMenuRadioItem id={`lantern-chat-model-${item.id}`} key={item.id} value={item.id} className="min-h-11"><span className="min-w-0 break-all text-xs">{item.id}</span></DropdownMenuRadioItem>)}</DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          {busy ? <Button id="lantern-chat-stop" type="button" size="icon" variant="secondary" className="size-11 rounded-lg" aria-label="Stop response" onClick={() => request.current?.abort()}><Square aria-hidden="true" className="size-3.5 fill-current" /></Button>
            : <Button id="lantern-chat-send" type="submit" size="icon" className="size-11 rounded-lg bg-foreground text-background hover:bg-foreground/85" disabled={!draft.trim()} aria-label="Send message"><ArrowUp aria-hidden="true" className="size-4" /></Button>}
        </div>
      </form>
      <p role="status" className="sr-only">{notice}</p>
      <p className="text-center text-[11px] leading-5 text-muted-foreground">{busy ? "Assistant is responding" : <><span className="hidden sm:inline">Enter to send · Shift + Enter for a new line · </span>This conversation stays in this tab.</>}</p>
    </div>
  </section>;
}
