"""Postgres job queue: idempotent enqueue, SKIP LOCKED claims, retries with backoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from scout.db import ConnFactory
from scout.models import Job

BACKOFF_SECONDS = (5, 30, 120)
MAX_ATTEMPTS = 3


class JobQueue:
    def __init__(self, conn_factory: ConnFactory):
        self._cf = conn_factory

    def enqueue(self, kind: str, payload: dict[str, Any], dedupe_key: str,
                run_after: datetime | None = None) -> int | None:
        """New job id, or None when a job with `dedupe_key` already exists."""
        with self._cf() as conn:
            row = conn.execute(
                "INSERT INTO agent_jobs (kind, payload, dedupe_key, run_after) VALUES (%s, %s, %s, coalesce(%s, now())) "
                "ON CONFLICT (dedupe_key) DO NOTHING RETURNING id",
                (kind, Jsonb(payload), dedupe_key, run_after),
            ).fetchone()
            conn.execute("SELECT pg_notify('scout_jobs', %s)", (kind,))
            conn.commit()
        return row["id"] if row else None

    def claim(self, worker_id: str) -> Job | None:
        with self._cf() as conn:
            row = conn.execute(
                "UPDATE agent_jobs SET status = 'running', attempts = attempts + 1, worker = %s, updated_at = now() "
                "WHERE id = (SELECT id FROM agent_jobs WHERE status = 'queued' AND run_after <= now() "
                "ORDER BY run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
                (worker_id,),
            ).fetchone()
            conn.commit()
        return Job(**row) if row else None

    def done(self, job_id: int, task_id: str | None = None) -> None:
        with self._cf() as conn:
            conn.execute(
                "UPDATE agent_jobs SET status = 'done', task_id = coalesce(%s, task_id), error = NULL, "
                "updated_at = now() WHERE id = %s",
                (task_id, job_id),
            )
            conn.commit()

    def fail(self, job_id: int, error: str) -> bool:
        """Reschedule with backoff; returns True when the job is now finally `failed`."""
        with self._cf() as conn:
            row = conn.execute("SELECT attempts FROM agent_jobs WHERE id = %s", (job_id,)).fetchone()
            attempts = row["attempts"] if row else MAX_ATTEMPTS
            final = attempts >= MAX_ATTEMPTS
            delay = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1]
            conn.execute(
                "UPDATE agent_jobs SET status = %s, error = %s, run_after = %s, updated_at = now() WHERE id = %s",
                ("failed" if final else "queued", error[:2000],
                 datetime.now(timezone.utc) + timedelta(seconds=delay), job_id),
            )
            conn.commit()
        return final

    def get(self, job_id: int) -> Job:
        with self._cf() as conn:
            return Job(**conn.execute("SELECT * FROM agent_jobs WHERE id = %s", (job_id,)).fetchone())
