"""Server-Sent Events feed for `GET /v1/events`: polls the database for new decisions, approvals and events.

Each of the three tables has its own cursor (timestamp, id), because their timestamps are not comparable: events carry
simulated time (December 2025), decisions and approvals carry the wall clock. The SSE `id:` of every message is the
complete cursor after that message, so `Last-Event-ID` resumes exactly where the client stopped.

An approval is emitted again when it is answered (its key is `COALESCE(resolved_at, created_at)`), with the resolved
decision embedded. A decision that changes in place (pending -> executed) is not emitted twice; follow the approval.

No threads and no timers here: `stream()` is a plain generator that calls `config.wait(interval)` between polls, so
tests drive it with a fake wait and never sleep.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from cfo.api import services
from cfo.api.deps import StreamConfig
from cfo.api.schemas import EventOut
from cfo.db.repo import Repository
from cfo.domain import ApprovalRequest, Decision, Event

Cursor = dict[
    str, tuple[datetime, str]
]  # source -> (key timestamp, row id) of the last emitted row

# source -> (table, key expression, SSE event name)
SOURCES: dict[str, tuple[str, str, str]] = {
    "decision": ("decisions", "ts", "decision"),
    "approval": ("approval_requests", "COALESCE(resolved_at, created_at)", "approval"),
    "event": ("events", "ts", "event"),
}


def encode_cursor(cursor: Cursor) -> str:
    """`decision=<iso ts>|<id>;approval=...;event=...` (sources without rows are omitted)."""
    return ";".join(
        f"{name}={ts.isoformat()}|{row_id}" for name, (ts, row_id) in cursor.items()
    )


def decode_cursor(text: str | None) -> Cursor | None:
    """None when `text` is empty or not a cursor of ours (the stream then starts from now)."""
    if not text:
        return None
    cursor: Cursor = {}
    try:
        for part in text.split(";"):
            name, value = part.split("=", 1)
            ts, row_id = value.split("|", 1)
            if name not in SOURCES or not row_id:
                return None
            cursor[name] = (datetime.fromisoformat(ts), row_id)
    except ValueError:
        return None
    return cursor


def current_cursor(repo: Repository) -> Cursor:
    """Position of the newest row of every table: a stream started with it only sees what is written afterwards."""
    cursor: Cursor = {}
    for name, (table, key, _) in SOURCES.items():
        row = repo.conn.execute(
            f"SELECT {key} AS k, id FROM {table} ORDER BY {key} DESC, id DESC LIMIT 1"
        ).fetchone()
        if row:
            cursor[name] = (row["k"], row["id"])
    return cursor


def _rows_after(
    repo: Repository, name: str, cursor: Cursor, limit: int
) -> list[dict[str, Any]]:
    table, key, _ = SOURCES[name]
    if name in cursor:
        ts, row_id = cursor[name]
        return repo._all(
            f"SELECT *, {key} AS _k FROM {table} WHERE ({key}, id) > (%s, %s) ORDER BY {key}, id LIMIT %s",
            [ts, row_id, limit],
        )
    return repo._all(
        f"SELECT *, {key} AS _k FROM {table} ORDER BY {key}, id LIMIT %s", [limit]
    )


def _payload(repo: Repository, name: str, row: dict[str, Any]) -> dict[str, Any]:
    row = {k: v for k, v in row.items() if k != "_k"}
    if name == "decision":
        return json.loads(services._decision_out(Decision(**row)).model_dump_json())
    if name == "approval":
        return json.loads(
            services.approval_out(repo, ApprovalRequest(**row)).model_dump_json()
        )
    ev = Event(**row)
    return json.loads(
        EventOut(id=ev.id, ts=ev.ts, kind=ev.kind, payload=ev.payload).model_dump_json()
    )


def format_message(event: str, data: dict[str, Any], cursor: Cursor) -> str:
    return f"id: {encode_cursor(cursor)}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def poll(repo: Repository, cursor: Cursor, limit: int) -> list[str]:
    """New messages since `cursor`; advances `cursor` in place, message by message."""
    out: list[str] = []
    for name, (_, _, sse_name) in SOURCES.items():
        for row in _rows_after(repo, name, cursor, limit):
            data = _payload(repo, name, row)
            cursor[name] = (row["_k"], row["id"])
            out.append(format_message(sse_name, data, cursor))
    return out


def stream(config: StreamConfig, last_event_id: str | None) -> Iterator[str]:
    """Yields SSE text chunks. Starts after `last_event_id`, or from now when it is missing or invalid.

    Runs in a worker thread of the server; when the client disconnects, Starlette stops iterating it.
    """
    cursor = decode_cursor(last_event_id)
    if cursor is None:
        with config.repo_factory() as repo:
            cursor = current_cursor(repo)
    yield "retry: 3000\n: connected\n\n"
    idle, polls = 0, 0
    while config.max_polls is None or polls < config.max_polls:
        if polls:
            config.wait(config.interval)
        polls += 1
        with config.repo_factory() as repo:
            messages = poll(repo, cursor, config.batch)
        if messages:
            idle = 0
            yield from messages
        else:
            idle += 1
            if idle >= config.keepalive_polls:
                idle = 0
                yield ": keepalive\n\n"
