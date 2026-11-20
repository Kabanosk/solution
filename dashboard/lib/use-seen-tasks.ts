"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import type { AgentTask } from "./scout-model";
import { baselineSeen, type SeenMap } from "./task-signals";

// Which task results the user has opened, per browser (localStorage), shared across tabs.
const KEY = "scout-seen-tasks";
const EMPTY: SeenMap = {};
const SERVER: SeenMap = {}; // Before hydration nothing is known; callers then show no "new result" dots.
const listeners = new Set<() => void>();
let cache: { raw: string | null; value: SeenMap } = { raw: null, value: EMPTY };

function read(): SeenMap {
  let raw: string | null = null;
  try { raw = localStorage.getItem(KEY); } catch { /* Storage blocked: nothing counts as seen. */ }
  if (raw !== cache.raw) {
    let value: SeenMap = EMPTY;
    try { const parsed = raw ? JSON.parse(raw) : null; if (parsed && typeof parsed === "object") value = parsed; } catch { /* Corrupt value: start over. */ }
    cache = { raw, value };
  }
  return cache.value;
}

function write(next: SeenMap) {
  try {
    // Keep the newest 500 entries so the map cannot grow without bound.
    const entries = Object.entries(next).sort((a, b) => b[1].localeCompare(a[1])).slice(0, 500);
    localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(entries)));
  } catch { /* The dots still work for this page view. */ }
  listeners.forEach(listener => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => { if (event.key === KEY || event.key === null) listener(); };
  window.addEventListener("storage", onStorage);
  return () => { listeners.delete(listener); window.removeEventListener("storage", onStorage); };
}

/** Seen map plus `markSeen(task)`. On the first visit the tasks finished so far are the baseline. */
export function useSeenTasks(tasks: Pick<AgentTask, "id" | "loop" | "status" | "ownerId" | "pendingApprovalFor" | "updatedAt" | "closedAt">[]) {
  const stored = useSyncExternalStore(subscribe, read, () => SERVER);
  const ready = stored !== SERVER;
  const seen = ready && cache.raw !== null ? stored : baselineSeen(tasks);
  useEffect(() => {
    let present = false;
    try { present = localStorage.getItem(KEY) !== null; } catch { present = true; }
    if (!present) write(baselineSeen(tasks));
  }, [tasks]);
  const markSeen = useCallback((task: Pick<AgentTask, "id" | "updatedAt" | "closedAt">) => {
    const stamp = [task.updatedAt, task.closedAt ?? ""].sort().at(-1)!;
    const current = read();
    if (current[task.id] && current[task.id] >= stamp) return;
    write({ ...current, [task.id]: stamp });
  }, []);
  return { seen, ready, markSeen };
}
