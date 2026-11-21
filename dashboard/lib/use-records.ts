"use client";

import { useEffect, useState } from "react";
import type { RecordKind, ScoutRecords } from "./scout-model";

// The audit log and the transaction list load outside the page payload. They are fetched once per tab (prefetched
// when the browser is idle, so the views usually open instantly) and refreshed in the background on each visit.
type Entry<K extends RecordKind> = { data?: ScoutRecords[K]; pending?: Promise<ScoutRecords[K]>; at: number };
const cache: { [K in RecordKind]?: Entry<K> } = {};
const listeners = new Set<() => void>();

function load<K extends RecordKind>(kind: K): Promise<ScoutRecords[K]> {
  const entry = (cache[kind] ?? { at: 0 }) as Entry<K>;
  if (entry.pending) return entry.pending;
  entry.pending = fetch(`/api/records/${kind}`, { cache: "no-store" }).then(async response => {
    if (!response.ok) throw new Error("Records unavailable");
    return await response.json() as ScoutRecords[K];
  }).then(data => { entry.data = data; entry.at = Date.now(); return data; })
    .finally(() => { entry.pending = undefined; listeners.forEach(listener => listener()); });
  (cache as Record<RecordKind, Entry<RecordKind>>)[kind] = entry as Entry<RecordKind>;
  return entry.pending;
}

/** Warm the cache without rendering anything (called when the browser is idle after the first paint). */
export function prefetchRecords(kind: RecordKind) {
  if (!cache[kind]?.data && !cache[kind]?.pending) load(kind).catch(() => {});
}

export function useRecords<K extends RecordKind>(kind: K): { data: ScoutRecords[K] | undefined; error: boolean; reload: () => void } {
  const [, setTick] = useState(0);
  const [error, setError] = useState(false);
  useEffect(() => {
    const listener = () => setTick(tick => tick + 1);
    listeners.add(listener);
    const entry = cache[kind];
    // Stale after 15 s: show what we have and refresh quietly.
    if (!entry?.data || Date.now() - entry.at > 15_000) load(kind).then(() => setError(false), () => setError(!cache[kind]?.data));
    return () => { listeners.delete(listener); };
  }, [kind]);
  return {
    data: cache[kind]?.data as ScoutRecords[K] | undefined, error,
    reload: () => { setError(false); load(kind).catch(() => setError(true)); },
  };
}
