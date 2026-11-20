"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpRight, Check, ChevronDown, Copy, Download, FileSpreadsheet, FileText, History, LoaderCircle, Send, Wrench, RotateCcw, Square, SquarePen } from "lucide-react";
import { ScoutMark } from "@/components/brand";
import { Markdown } from "@/components/markdown";
import { applyCompaction, CHAT_MODELS, MAX_MESSAGE_LENGTH, readJsonLines, requestFor, type ChatFile, type ChatTurn } from "@/lib/chat-model";
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
    if (input.current) { input.current.style.height = "auto"; input.current.style.height = `${Math.min(input.current.scrollHeight, 160)}px`; }
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
    const steps: string[] = [];
    const files: ChatFile[] = [];
    let completed = false;
    let failure = "Could not reach Scout. Try again.";
    const request_ = requestFor(history);
    try {
      const response = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model, messages: request_.messages }), signal: controller.signal });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        if (typeof body?.error === "string") failure = body.error;
        throw new Error("Chat request failed");
      }
      if (!response.body) throw new Error("No response stream");
      failure = "The response was interrupted. Try again.";
      for await (const event of readJsonLines(response.body)) {
        if (controller.signal.aborted) break;
        if (event.type === "step" && typeof event.text === "string") { steps.push(event.text); update({ steps: [...steps] }); }
        else if (event.type === "delta" && typeof event.text === "string") { content += event.text; update({ content }); }
        else if (event.type === "compacted") {
          const summaryId = crypto.randomUUID();
          setMessages(current => applyCompaction(current, request_.sources, event, summaryId));
          setNotice("Earlier messages were summarized to fit the model.");
        }
        else if (event.type === "file") { files.push({ name: event.name, mime: event.mime, dataB64: event.data_b64, sentToSlack: event.sent_to_slack }); update({ files: [...files] }); }
        else if (event.type === "error") { failure = event.message; throw new Error("Model stream failed"); }
        else if (event.type === "done") { completed = true; setTruncated(event.truncated); if (event.task_id) update({ taskId: event.task_id }); }
      }
      if (request.current !== controller) return;
      if (controller.signal.aborted) update({ status: "stopped" });
      else if (!completed || (!content.trim() && !files.length)) throw new Error("Incomplete response");
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
  const modelName = CHAT_MODELS.find(item => item.id === model)?.name ?? model;

  return <section id="scout-llm-chat" aria-labelledby="scout-chat-title" className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-xl border bg-card">
    <header id="scout-chat-header" className="flex shrink-0 items-center gap-3 border-b px-4 py-2.5">
      <ScoutMark size={22} />
      <div className="min-w-0 flex-1 leading-tight">
        <h1 id="scout-chat-title" className="text-[13px] font-medium">Scout chat</h1>
        <p className="truncate text-[11px] text-muted-foreground">Answers with its tools · {modelName}</p>
      </div>
      <Button id="scout-chat-new" variant="ghost" size="sm" onClick={newChat} disabled={messages.length === 0 && !busy} className="h-8 gap-1.5 text-muted-foreground"><SquarePen className="size-4" aria-hidden="true" /><span>New chat</span></Button>
    </header>

    <div id="scout-chat-conversation" ref={viewport} role="region" aria-label="Conversation" tabIndex={0} className="min-h-0 flex-1 overflow-y-auto overscroll-contain bg-background/40 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset" onScroll={() => {
      const element = viewport.current;
      if (element) { follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; setFollowing(follow.current); }
    }}>
      {messages.length === 0 ? <div id="scout-chat-empty" className="flex h-full min-h-44 flex-col items-center justify-center gap-5 px-4 py-6 text-center">
        <ScoutMark size={40} />
        <div className="space-y-1.5"><h2 className="text-xl font-medium tracking-tight">Ask Scout</h2><p className="max-w-md text-sm text-muted-foreground">Scout answers with its tools: missing invoices, cash forecast, tasks and costs.</p></div>
        <div id="scout-chat-suggestions" className="flex max-w-xl flex-wrap justify-center gap-2">
          {SUGGESTIONS.map((suggestion, index) => <Button key={suggestion} id={`scout-chat-suggestion-${index}`} type="button" variant="outline" size="sm" className="h-auto rounded-full px-3 py-1.5 text-xs font-normal text-muted-foreground hover:text-foreground"
            onClick={() => { setDraft(suggestion); input.current?.focus(); }}>{suggestion}</Button>)}
        </div>
      </div> : <ol id="scout-chat-messages" className="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6">
        {messages.map(turn => turn.kind === "summary"
          ? <li key={turn.id} id={`scout-chat-message-${turn.id}`}><SummaryDivider turn={turn} /></li>
          : turn.role === "user"
          ? <li key={turn.id} id={`scout-chat-message-${turn.id}`} className="flex justify-end">
              <UserBubble content={turn.content} />
            </li>
          : <li key={turn.id} id={`scout-chat-message-${turn.id}`} className="flex min-w-0 gap-3">
              <ScoutMark size={26} className="mt-0.5" />
              <div className="min-w-0 flex-1 space-y-2">
                <div className="flex items-center gap-2 text-xs text-muted-foreground"><span className="font-medium text-foreground">Scout</span>{turn.status === "stopped" && <span>· Stopped</span>}{turn.status === "error" && <span className="text-danger">· Interrupted</span>}</div>
                {turn.steps && turn.steps.length > 0 && <details open={turn.status === "pending"} className="group rounded-lg border bg-background/60 text-[12px]">
                  <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-1.5 text-muted-foreground hover:text-foreground">
                    {turn.status === "pending" && !turn.content ? <LoaderCircle aria-hidden="true" className="size-3.5 motion-safe:animate-spin" /> : <Wrench aria-hidden="true" className="size-3.5" />}
                    <span>{turn.status === "pending" && !turn.content ? "Using tools" : `Used ${turn.steps.length} ${turn.steps.length === 1 ? "tool" : "tools"}`}</span>
                    <ChevronDown aria-hidden="true" className="ml-auto size-3.5 transition-transform group-open:rotate-180" />
                  </summary>
                  <ul aria-label="Scout tool calls" className="space-y-1 border-t px-3 py-2">
                    {turn.steps.map((step, index) => <li key={index} className="flex items-start gap-2 font-mono leading-5 text-muted-foreground">
                      {turn.status === "pending" && index === turn.steps!.length - 1 && !turn.content
                        ? <LoaderCircle aria-hidden="true" className="mt-1 size-3 shrink-0 motion-safe:animate-spin" />
                        : <Check aria-hidden="true" className="mt-1 size-3 shrink-0 text-success" />}
                      <span className="min-w-0 break-words">{step}</span>
                    </li>)}
                  </ul>
                </details>}
                {turn.files?.length ? <div className="space-y-1.5">
                  <div className="flex flex-wrap gap-2">{turn.files.map((file, index) => <FileChip key={index} file={file} />)}</div>
                  {turn.files.some(file => file.sentToSlack) && <p className="flex items-center gap-1.5 pl-1 text-[11px] text-muted-foreground"><Send className="size-3" aria-hidden="true" />{turn.files.length === 1 ? "Also sent to your Slack DM" : `All ${turn.files.length} files also sent to your Slack DM`}</p>}
                </div> : null}
                {turn.content ? <Markdown source={turn.content} className="min-w-0 text-sm leading-6 [overflow-wrap:anywhere]" />
                  : turn.files?.length ? null : turn.status === "pending" ? <span className="inline-flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle aria-hidden="true" className="size-3.5 motion-safe:animate-spin" />Working…</span>
                  : <span className="text-sm text-muted-foreground">No response received.</span>}
                {(turn.taskId || (turn.content && turn.status !== "pending")) && <div className="flex items-center gap-1">
                  {turn.content && turn.status !== "pending" && <Button variant="ghost" size="sm" aria-label="Copy response" className="h-7 px-2 text-muted-foreground" onClick={() => void copy(turn)}>{copied === turn.id ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}</Button>}
                  {turn.taskId && <Link href={`/#task/${encodeURIComponent(turn.taskId)}`} className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">Open {turn.taskId}<ArrowUpRight aria-hidden="true" className="size-3" /></Link>}
                </div>}
              </div>
            </li>)}
      </ol>}
    </div>

    <div className="shrink-0 border-t bg-card px-3 pt-3 pb-2 sm:px-4">
      <div className="mx-auto w-full max-w-3xl space-y-2">
        {!following && messages.length > 0 && <div className="-mt-12 flex justify-center"><Button id="scout-chat-latest" variant="outline" size="sm" className="h-8 gap-2 rounded-full shadow-sm" onClick={scrollToEnd}><ArrowDown aria-hidden="true" className="size-3.5" />Latest</Button></div>}
        {error && <p id="scout-chat-error" role="alert" className="text-sm leading-6 text-danger">{error}</p>}
        {!busy && retryable && <Button id="scout-chat-retry" variant="ghost" size="sm" className="h-8 text-muted-foreground" onClick={() => void reply(messages.slice(0, -1))}><RotateCcw aria-hidden="true" className="size-3.5" />Try again</Button>}
        {truncated && <p role="status" className="text-xs text-muted-foreground">Response reached its length limit. Send a follow-up to continue.</p>}
        <form id="scout-chat-composer" className="rounded-xl border bg-background p-1.5 shadow-xs focus-within:border-ring" onSubmit={event => { event.preventDefault(); send(); }}>
          <label className="sr-only" htmlFor="scout-chat-input">Message Scout</label>
          <div className="flex items-end gap-2">
            <Textarea id="scout-chat-input" ref={input} rows={1} value={draft} maxLength={MAX_MESSAGE_LENGTH} placeholder="Ask Scout, e.g. which payments are missing invoices?" className="max-h-40 min-h-10 flex-1 resize-none border-0 bg-transparent px-2 py-2 shadow-none focus-visible:ring-0" onChange={event => setDraft(event.target.value)} onKeyDown={event => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
            }} />
            {busy ? <Button id="scout-chat-stop" type="button" size="icon" variant="secondary" className="size-9 shrink-0 rounded-lg" aria-label="Stop response" onClick={() => request.current?.abort()}><Square aria-hidden="true" className="size-3.5 fill-current" /></Button>
              : <Button id="scout-chat-send" type="submit" size="icon" className="size-9 shrink-0 rounded-lg" disabled={!draft.trim()} aria-label="Send message"><ArrowUp aria-hidden="true" className="size-4" /></Button>}
          </div>
        </form>
        <div className="flex min-w-0 items-center justify-between gap-3">
          <DropdownMenu><DropdownMenuTrigger asChild><Button id="scout-chat-model" type="button" variant="ghost" size="sm" aria-label="Select model" className="h-7 min-w-0 gap-1.5 px-2 text-[11px] font-normal text-muted-foreground" disabled={busy}><span className="truncate">{modelName}</span><ChevronDown aria-hidden="true" className="size-3" /></Button></DropdownMenuTrigger>
            <DropdownMenuContent id="scout-chat-model-menu" side="top" align="start" className="max-w-[calc(100vw-2rem)]">
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">Available models</DropdownMenuLabel>
              <DropdownMenuRadioGroup value={model} onValueChange={setModel}>{CHAT_MODELS.map(item => <DropdownMenuRadioItem id={`scout-chat-model-${item.id}`} key={item.id} value={item.id} className="min-h-9"><span className="min-w-0 break-all text-xs">{item.id}</span></DropdownMenuRadioItem>)}</DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <p className="truncate text-[11px] text-muted-foreground">{busy ? "Scout is working…" : <><span className="hidden sm:inline">Enter to send · Shift + Enter for a new line · </span>Stays in this tab</>}</p>
        </div>
        <p role="status" className="sr-only">{notice}</p>
      </div>
    </div>
  </section>;
}

const SUGGESTIONS = [
  "Which payments are missing invoices?",
  "What does the cash forecast look like for the next 90 days?",
  "Which tasks are waiting on me?",
  "What did we spend on beverages this quarter?",
];

function FileChip({ file }: { file: ChatFile }) {
  const size = Math.floor(file.dataB64.length * 3 / 4) - (file.dataB64.endsWith("==") ? 2 : file.dataB64.endsWith("=") ? 1 : 0);
  const Icon = /csv|sheet/.test(file.mime) ? FileSpreadsheet : FileText;
  function download() {
    const bytes = Uint8Array.from(atob(file.dataB64), char => char.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: file.mime }));
    const link = document.createElement("a");
    link.href = url; link.download = file.name; link.rel = "noopener";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <div data-chat-file={file.name} className="flex w-72 max-w-full items-center gap-3 rounded-lg border bg-background px-3 py-2">
    <span className={`flex size-8 shrink-0 items-center justify-center rounded-md border ${file.mime === "application/pdf" ? "border-danger-border text-danger" : "border-success-border bg-success-muted text-success"}`}><Icon className="size-4" aria-hidden="true" /></span>
    <span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-medium" title={file.name}>{file.name}</span><span className="block text-[11px] text-muted-foreground">{file.mime === "text/csv" ? "CSV" : file.mime === "application/pdf" ? "PDF" : file.mime.split("/").pop()} · {size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`}</span></span>
    <Button type="button" variant="ghost" size="icon" className="size-8 shrink-0" onClick={download} aria-label={`Download ${file.name}`} title="Download"><Download className="size-4" aria-hidden="true" /></Button>
  </div>;
}

/** Long pasted messages fold to a few lines, so the conversation stays readable. */
function UserBubble({ content }: { content: string }) {
  const [open, setOpen] = useState(false);
  const long = content.length > 600 || content.split("\n").length > 8;
  return <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm leading-6 text-primary-foreground">
    <div className={`whitespace-pre-wrap [overflow-wrap:anywhere] ${long && !open ? "line-clamp-6" : ""}`}><span className="sr-only">You: </span>{content}</div>
    {long && <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)} className="mt-1 text-xs text-primary-foreground/80 underline underline-offset-2 hover:text-primary-foreground">
      {open ? "Show less" : `Show all · ${content.length.toLocaleString("en-US")} characters`}</button>}
  </div>;
}

/** Scout compacted the earlier conversation: a thin divider that opens the summary. */
function SummaryDivider({ turn }: { turn: ChatTurn }) {
  const [open, setOpen] = useState(false);
  const count = turn.replaced ?? 0;
  return <div className="space-y-2">
    <button type="button" aria-expanded={open} onClick={() => setOpen(value => !value)} data-chat-summary
      className="group flex w-full items-center gap-3 text-[11px] text-muted-foreground hover:text-foreground">
      <span className="h-px flex-1 bg-border" aria-hidden="true" />
      <span className="inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-1"><History className="size-3" aria-hidden="true" />Earlier messages summarized · {count} {count === 1 ? "message" : "messages"}<ChevronDown aria-hidden="true" className={`size-3 transition-transform ${open ? "rotate-180" : ""}`} /></span>
      <span className="h-px flex-1 bg-border" aria-hidden="true" />
    </button>
    {open && <div className="rounded-lg border bg-muted/40 px-4 py-3"><p className="mb-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">Summary Scout keeps as context</p><Markdown source={turn.content} className="text-[13px] leading-6" /></div>}
  </div>;
}
