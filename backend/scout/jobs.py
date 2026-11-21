"""Postgres job queue: idempotent enqueue, SKIP LOCKED claims, retries with backoff.

A claim is a lease: the worker renews it while the handler runs (`renew`), so a job left `running` for longer than
`lease_seconds(kind)` lost its worker (the pod died mid-job). It is claimed again, as a new attempt, while it has
attempts left; on the last attempt `reap_expired` hands it to a worker under a new lease, and the worker ends it
`failed` (`fail`) only after it recorded the failure, so a reaper that dies leaves the job to be reaped again. The
attempt number is the claim's fencing token: `done`, `fail` and `renew` of a claim that was taken over change nothing.
Handlers are written to be safe to replay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from scout.db import ConnFactory
from scout.models import Job

BACKOFF_SECONDS = (5, 30, 120)
MAX_ATTEMPTS = 3
LEASE_SECONDS = 15 * 60  # far above the longest job (a KSeF sync polls for ~30 s; chat turns take seconds)
# A Slack reply fallback's lease outlives its reply claim (`dm_replies.LEASE_S`), so a retry after a dead worker finds
# that claim expired and sends.
LEASES = {"dm_reply_notify": 180}
_LEASE_SQL = "make_interval(secs => coalesce((%s::jsonb ->> kind)::int, %s))"  # the lease of the row's kind


def lease_seconds(kind: str) -> int:
    return LEASES.get(kind, LEASE_SECONDS)


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
                "WHERE id = (SELECT id FROM agent_jobs WHERE (status = 'queued' AND run_after <= now()) "
                f"OR (status = 'running' AND updated_at < now() - {_LEASE_SQL} AND attempts < %s) "
                "ORDER BY run_after, id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
                (worker_id, Jsonb(LEASES), LEASE_SECONDS, MAX_ATTEMPTS),
            ).fetchone()
            conn.commit()
        return Job(**row) if row else None

    def reap_expired(self) -> list[Job]:
        """Jobs whose worker died on their last attempt (lease expired, no attempts left), leased to the caller so
        it records the failure (escalate the task, end a KSeF run) and then ends them with `fail(attempt=...)`.
        Reaping bumps the attempt number (the fencing token), so the expired worker can no longer renew or finish
        the job. Until `fail`, the job stays `running`: a caller that dies leaves it to be reaped again."""
        with self._cf() as conn:
            rows = conn.execute(
                "UPDATE agent_jobs SET updated_at = now(), attempts = attempts + 1 WHERE id IN (SELECT id FROM agent_jobs "
                f"WHERE status = 'running' AND updated_at < now() - {_LEASE_SQL} AND attempts >= %s "
                "FOR UPDATE SKIP LOCKED) RETURNING *",
                (Jsonb(LEASES), LEASE_SECONDS, MAX_ATTEMPTS),
            ).fetchall()
            conn.commit()
        return [Job(**r) for r in rows]

    @staticmethod
    def _fence(attempt: int | None) -> tuple[str, tuple[Any, ...]]:
        return ("", ()) if attempt is None else (" AND status = 'running' AND attempts = %s", (attempt,))

    def renew(self, job_id: int, attempt: int) -> bool:
        """Extend the lease of a claim; False when another worker took the job over (or it finished)."""
        with self._cf() as conn:
            row = conn.execute("UPDATE agent_jobs SET updated_at = now() WHERE id = %s AND status = 'running' "
                               "AND attempts = %s RETURNING id", (job_id, attempt)).fetchone()
            conn.commit()
        return row is not None

    def done(self, job_id: int, task_id: str | None = None, *, attempt: int | None = None) -> bool:
        """Mark done. With `attempt` (the claim's), only while that claim still holds the job; False otherwise."""
        fence, args = self._fence(attempt)
        with self._cf() as conn:
            row = conn.execute(
                "UPDATE agent_jobs SET status = 'done', task_id = coalesce(%s, task_id), error = NULL, "
                f"updated_at = now() WHERE id = %s{fence} RETURNING id",
                (task_id, job_id, *args),
            ).fetchone()
            conn.commit()
        return row is not None

    def fail(self, job_id: int, error: str, *, attempt: int | None = None) -> bool:
        """Reschedule with backoff; returns True when the job is now finally `failed`. With `attempt` (the claim's),
        a claim that was taken over changes nothing and returns False."""
        fence, args = self._fence(attempt)
        with self._cf() as conn:
            row = conn.execute(f"SELECT attempts FROM agent_jobs WHERE id = %s{fence} FOR UPDATE",
                               (job_id, *args)).fetchone()
            if row is None and attempt is not None:
                conn.rollback()
                return False
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
