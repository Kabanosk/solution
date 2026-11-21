"use client";
import { useEffect, useState } from "react";
import type { RecordKind, ScoutRecords } from "./scout-model";
import { scopeKey, tenantFetch } from "./tenant-browser";

type Entry = { data?: ScoutRecords[RecordKind]; pending?: Promise<ScoutRecords[RecordKind]>; at: number };
const cache = new Map<string, Entry>();
const listeners = new Set<() => void>();
function load<K extends RecordKind>(kind: K): Promise<ScoutRecords[K]> {
  const key = scopeKey(kind), entry = cache.get(key) ?? { at: 0 };
  if (entry.pending) return entry.pending as Promise<ScoutRecords[K]>;
  entry.pending = tenantFetch(`/api/records/${kind}`).then(async response => {
    if (!response.ok) throw new Error("Records unavailable");
    return await response.json() as ScoutRecords[K];
  }).then(data => { entry.data = data; entry.at = Date.now(); return data; })
    .finally(() => { entry.pending = undefined; listeners.forEach(listener => listener()); });
  cache.set(key, entry);
  return entry.pending as Promise<ScoutRecords[K]>;
}
export function prefetchRecords(kind: RecordKind) {
  const entry = cache.get(scopeKey(kind));
  if (!entry?.data && !entry?.pending) load(kind).catch(() => {});
}
export function useRecords<K extends RecordKind>(kind: K): { data: ScoutRecords[K] | undefined; error: boolean; reload: () => void } {
  const [, setTick] = useState(0), [error, setError] = useState(false);
  useEffect(() => {
    const listener = () => setTick(tick => tick + 1);
    listeners.add(listener);
    const entry = cache.get(scopeKey(kind));
    if (!entry?.data || Date.now() - entry.at > 15000) load(kind).then(() => setError(false), () => setError(!cache.get(scopeKey(kind))?.data));
    return () => { listeners.delete(listener); };
  }, [kind]);
  return { data: cache.get(scopeKey(kind))?.data as ScoutRecords[K] | undefined, error, reload: () => { setError(false); load(kind).catch(() => setError(true)); } };
}
