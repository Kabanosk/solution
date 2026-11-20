"""Agent tasks and their step log. Code writes each step at the moment it happens (never an LLM summary)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from psycopg.types.json import Jsonb

from scout.db import ConnFactory, notify
from scout.models import Step, Task

log = logging.getLogger(__name__)
Listener = Callable[[Task, Step], None]

_TASK_FIELDS = {
    "owner_id", "requested_by", "category", "amount", "currency", "counterparty", "transaction_id",
    "slack_ref", "approval_id", "level", "simulated", "status", "title",
}


class TaskRecorder:
    def __init__(self, conn_factory: ConnFactory):
        self._cf = conn_factory
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def add_listener(self, fn: Listener) -> None:
        with self._lock:
            self._listeners.append(fn)

    # -- tasks -----------------------------------------------------------------------------------------
    def open(self, dedupe_key: str, loop: str, title: str, **fields: Any) -> Task:
        """The task for `dedupe_key`: the existing one (a continuation) or a new one."""
        unknown = set(fields) - _TASK_FIELDS
        if unknown:
            raise TypeError(f"unknown task fields: {sorted(unknown)}")
        cols = ["dedupe_key", "loop", "title", *fields]
        vals = [dedupe_key, loop, title, *fields.values()]
        with self._cf() as conn:
            # look first, so a continuation does not burn a task number from the sequence
            row = conn.execute("SELECT * FROM agent_tasks WHERE dedupe_key = %s", (dedupe_key,)).fetchone()
            if row is None:
                row = conn.execute(
                    f"INSERT INTO agent_tasks ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                    "ON CONFLICT (dedupe_key) DO NOTHING RETURNING *",
                    vals,
                ).fetchone() or conn.execute(
                    "SELECT * FROM agent_tasks WHERE dedupe_key = %s", (dedupe_key,)
                ).fetchone()
                notify(conn, "scout_tasks", row["id"])
            conn.commit()
        return Task(**row)

    def get(self, task_id: str) -> Task:
        with self._cf() as conn:
            row = conn.execute("SELECT * FROM agent_tasks WHERE id = %s", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return Task(**row)

    def by_key(self, dedupe_key: str) -> Task | None:
        with self._cf() as conn:
            row = conn.execute("SELECT * FROM agent_tasks WHERE dedupe_key = %s", (dedupe_key,)).fetchone()
        return Task(**row) if row else None

    def find_open(self, *, owner_id: str | None = None, statuses: tuple[str, ...] = ("waiting", "escalated"),
                  slack_ref: str | None = None) -> list[Task]:
        sql, args = "SELECT * FROM agent_tasks WHERE status = ANY(%s)", [list(statuses)]
        if owner_id:
            sql += " AND owner_id = %s"
            args.append(owner_id)
        if slack_ref:
            sql += " AND slack_ref = %s"
            args.append(slack_ref)
        with self._cf() as conn:
            rows = conn.execute(sql + " ORDER BY updated_at DESC", args).fetchall()
        return [Task(**r) for r in rows]

    def update(self, task_id: str, **fields: Any) -> Task:
        unknown = set(fields) - _TASK_FIELDS - {"outcome", "resolved_via", "evidence"}
        if unknown:
            raise TypeError(f"unknown task fields: {sorted(unknown)}")
        sets = [f"{k} = %s" for k in fields] + ["updated_at = now()"]
        vals = [Jsonb(v) if k == "evidence" else v for k, v in fields.items()]
        if fields.get("status") in ("done", "cancelled"):
            sets.append("closed_at = coalesce(closed_at, now())")
        with self._cf() as conn:
            row = conn.execute(
                f"UPDATE agent_tasks SET {', '.join(sets)} WHERE id = %s RETURNING *", [*vals, task_id]
            ).fetchone()
            notify(conn, "scout_tasks", task_id)
            conn.commit()
        if row is None:
            raise KeyError(task_id)
        return Task(**row)

    def set_status(self, task_id: str, status: str, *, outcome: str | None = None, resolved_via: str | None = None,
                   level: str | None = None, owner_id: str | None = None) -> Task:
        fields: dict[str, Any] = {"status": status}
        for k, v in (("outcome", outcome), ("resolved_via", resolved_via), ("level", level), ("owner_id", owner_id)):
            if v is not None:
                fields[k] = v
        return self.update(task_id, **fields)

    def add_evidence(self, task_id: str, item: dict[str, Any]) -> None:
        with self._cf() as conn:
            conn.execute(
                "UPDATE agent_tasks SET evidence = evidence || %s::jsonb, updated_at = now() WHERE id = %s",
                (Jsonb([item]), task_id),
            )
            conn.commit()

    # -- steps -----------------------------------------------------------------------------------------
    def step(self, task_id: str, kind: str, text: str, *, source: str | None = None, result_count: int | None = None,
             detail: dict[str, Any] | None = None, evidence: dict[str, Any] | None = None) -> Step:
        with self._cf() as conn:
            row = conn.execute(
                "INSERT INTO agent_task_steps (task_id, kind, source, result_count, text, detail, evidence) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (task_id, kind, source, result_count, text, Jsonb(detail or {}),
                 Jsonb(evidence) if evidence is not None else None),
            ).fetchone()
            task_row = conn.execute(
                "UPDATE agent_tasks SET updated_at = now() WHERE id = %s RETURNING *", (task_id,)
            ).fetchone()
            notify(conn, "scout_tasks", task_id)
            conn.commit()
        step, task = Step(**row), Task(**task_row)
        if evidence:
            self.add_evidence(task_id, evidence)
        for fn in list(self._listeners):
            try:
                fn(task, step)
            except Exception:  # noqa: BLE001 - a listener (Slack edit) must never break the loop
                log.exception("task listener failed")
        return step

    def steps(self, task_id: str) -> list[Step]:
        with self._cf() as conn:
            rows = conn.execute("SELECT * FROM agent_task_steps WHERE task_id = %s ORDER BY id", (task_id,)).fetchall()
        return [Step(**r) for r in rows]
