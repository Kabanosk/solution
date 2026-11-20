"""Per-call recording of LLM usage. Called by `cfo.llm`; it must never break an LLM call.

* `usage_context(event_id=..., event_type=..., purpose=..., decision_id=...)`: a context manager (a context
  variable, so it follows LangGraph's worker threads) that says which agent step the next LLM calls belong to.
* `record_call(...)`: builds a `UsageRecord` and hands it to the sink. Every failure is logged and swallowed.
* The default sink writes to the `llm_usage` table with a short connect timeout and a circuit breaker: after a
  failed write the database is not tried again for `cooldown` seconds, so an unreachable DB costs one failed
  connect, not one per call. Tests replace the sink with `set_sink(...)` and inject the clocks.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from cfo.domain import utcnow

log = logging.getLogger("cfo.usage")

CHARS_PER_TOKEN = 4  # tokenizer-free heuristic for English / JSON text; only used when the server sent no usage

_CONTEXT_KEYS = ("event_id", "event_type", "purpose", "decision_id")
_context: ContextVar[dict[str, str]] = ContextVar("llm_usage_context", default={})

# Clocks, replaceable in tests.
now: Callable[[], datetime] = utcnow
perf_counter: Callable[[], float] = time.perf_counter


@contextmanager
def usage_context(**fields: str | None) -> Iterator[None]:
    """Tag LLM calls made inside the block. Nested blocks merge; `None` values leave the outer value alone."""
    unknown = set(fields) - set(_CONTEXT_KEYS)
    if unknown:
        raise TypeError(f"unknown usage context keys: {sorted(unknown)}")
    merged = {
        **_context.get(),
        **{k: str(v) for k, v in fields.items() if v is not None},
    }
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, str]:
    return dict(_context.get())


@dataclass(frozen=True)
class UsageRecord:
    ts: datetime
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    estimated: bool = False  # tokens are a heuristic (no `usage` field from the server)
    success: bool = True
    event_id: str | None = None
    event_type: str | None = None
    purpose: str | None = None
    decision_id: str | None = None


class UsageSink(Protocol):
    def write(self, record: UsageRecord) -> None: ...


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def _message_text(messages: Sequence[Any], tools: Sequence[Any] | None) -> str:
    parts: list[str] = []
    for m in messages:
        parts.append(getattr(m, "content", None) or "")
        for c in getattr(m, "tool_calls", None) or []:
            parts.append(c.name + json.dumps(c.arguments, default=str))
    for t in tools or []:
        parts.append(
            json.dumps(
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
                default=str,
            )
        )
    return "\n".join(parts)


def _response_text(response: Any) -> str:
    parts = [getattr(response, "content", None) or ""]
    for c in getattr(response, "tool_calls", None) or []:
        parts.append(c.name + json.dumps(c.arguments, default=str))
    return "\n".join(parts)


class DbSink:
    """Writes to the `llm_usage` table; skips the database for `cooldown` seconds after a failure."""

    def __init__(
        self,
        connect: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
        cooldown: float = 30.0,
    ):
        self._connect = connect or self._default_connect
        self._clock = clock
        self._cooldown = cooldown
        self._blocked_until = 0.0

    @staticmethod
    def _default_connect() -> Any:
        import psycopg
        from cfo.config import get_settings

        return psycopg.connect(get_settings().database_url, connect_timeout=3)

    def _run(self, work: Callable[[Any], None]) -> None:
        if self._clock() < self._blocked_until:
            return
        try:
            conn = self._connect()
            try:
                work(conn)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - recording is best-effort by contract
            self._blocked_until = self._clock() + self._cooldown
            log.warning(
                "llm usage not recorded (database skipped for %.0f s): %s",
                self._cooldown,
                exc,
            )

    def write(self, record: UsageRecord) -> None:
        from cfo.usage import store

        self._run(lambda conn: store.insert_usage(conn, record))

    def link(self, event_id: str, decision_id: str) -> None:
        from cfo.usage import store

        self._run(lambda conn: store.link_decision(conn, event_id, decision_id))


_sink: UsageSink | None = None
_default_sink: DbSink | None = None


def set_sink(sink: UsageSink | None) -> None:
    """Replace the sink (tests); `None` restores the default database sink."""
    global _sink
    _sink = sink


def _active_sink() -> UsageSink | None:
    global _default_sink
    if _sink is not None:
        return _sink
    from cfo.usage.settings import get_usage_settings

    if not get_usage_settings().llm_usage_record:
        return None
    if _default_sink is None:
        _default_sink = DbSink()
    return _default_sink


def record_call(
    *,
    model: str,
    messages: Sequence[Any],
    tools: Sequence[Any] | None,
    response: Any | None,
    started: float,
    success: bool = True,
    usage: Any | None = None,
) -> None:
    """Record one LLM call. `started` is a `perf_counter()` reading from before the call. Never raises."""
    try:
        sink = _active_sink()
        if sink is None:
            return
        latency_ms = max(0.0, (perf_counter() - started) * 1000.0)
        usage = usage if usage is not None else getattr(response, "usage", None)
        estimated = usage is None
        if usage is not None:
            prompt, completion = int(usage.prompt_tokens), int(usage.completion_tokens)
        else:
            prompt = estimate_tokens(_message_text(messages, tools))
            completion = (
                estimate_tokens(_response_text(response)) if response is not None else 0
            )
        ctx = _context.get()
        sink.write(
            UsageRecord(
                ts=now(),
                model=model,
                prompt_tokens=prompt,
                completion_tokens=completion,
                latency_ms=latency_ms,
                estimated=estimated,
                success=success,
                **{k: ctx.get(k) for k in _CONTEXT_KEYS},
            )
        )
    except Exception:  # noqa: BLE001
        log.warning("llm usage not recorded", exc_info=True)


def link_decision(event_id: str, decision_ids: Sequence[str]) -> None:
    """After an event was handled: attribute its so far unattributed LLM calls to the decision it ended with."""
    try:
        sink = _active_sink()
        link = getattr(sink, "link", None)
        if link is not None and decision_ids:
            link(event_id, decision_ids[-1])
    except Exception:  # noqa: BLE001
        log.warning("llm usage not linked to a decision", exc_info=True)
