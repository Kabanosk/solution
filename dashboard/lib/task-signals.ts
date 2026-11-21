// Pure task-list filtering and the sidebar loop indicators. No React, no storage: testable with node:test.
import type { AgentTask } from "./scout-model";
import type { TaskFilter } from "./nav";

type Task = Pick<AgentTask, "id" | "loop" | "status" | "ownerId" | "pendingApprovalFor" | "updatedAt" | "closedAt">;

/** Waiting on someone, escalated, or an approval is pending: a person has to react. */
export const needsReaction = (task: Pick<AgentTask, "status" | "pendingApprovalFor">) =>
  task.status === "waiting" || task.status === "escalated" || (!!task.pendingApprovalFor && task.status !== "done" && task.status !== "cancelled");

/** Inbox: tasks needing the CEO's reaction (owned by or waiting on an approval from them). */
export const inInbox = (task: Task, ceo: string) =>
  (task.status === "waiting" || task.status === "escalated") && (task.ownerId === ceo || task.pendingApprovalFor === ceo);

export function filterTasks<T extends Task>(tasks: T[], filter: TaskFilter, ceo: string): T[] {
  return tasks.filter(task => (!filter.scope || (filter.scope === "inbox" ? inInbox(task, ceo) : task.ownerId === ceo))
    && (!filter.loop || task.loop === filter.loop) && (!filter.status || task.status === filter.status)
    && (!filter.owner || task.ownerId === filter.owner));
}

/** Task id -> the task's updatedAt when the user last opened it. */
export type SeenMap = Record<string, string>;

/** Finished (done) and its result has not been opened since it finished. */
export function unseenResult(task: Task, seen: SeenMap): boolean {
  if (task.status !== "done") return false;
  const finishedAt = task.closedAt ?? task.updatedAt;
  const opened = seen[task.id];
  return !opened || opened < finishedAt;
}

export type LoopSignal = "attention" | "result" | "running" | null;
export const LOOP_SIGNAL_LABEL: Record<Exclude<LoopSignal, null>, string> = {
  attention: "Needs your reaction", result: "New result to review", running: "Working",
};

/** One dot per loop: amber needs a reaction, then green for an unseen result, then grey (pulsing) while working. */
export function loopSignal(tasks: Task[], loop: string, seen: SeenMap): { signal: LoopSignal; attention: number; results: number; running: number } {
  const own = tasks.filter(task => task.loop === loop);
  const attention = own.filter(needsReaction).length;
  const results = own.filter(task => unseenResult(task, seen)).length;
  const running = own.filter(task => task.status === "in_progress").length;
  const signal: LoopSignal = attention ? "attention" : results ? "result" : running ? "running" : null;
  return { signal, attention, results, running };
}

/** First visit: today's finished tasks count as seen, so only results finished from now on light up. */
export function baselineSeen(tasks: Task[]): SeenMap {
  return Object.fromEntries(tasks.filter(task => task.status === "done").map(task => [task.id, task.closedAt ?? task.updatedAt]));
}
