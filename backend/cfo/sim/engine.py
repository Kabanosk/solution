"""Event simulator: replays a time-ordered list of events into Postgres on a simulated clock.

State (sim time, paused, speed, replay cursor) lives in the table `sim_state`, so the API process, the CLI and
the worker that runs `Simulator.run` share it. The simulator never calls the agent: it writes each event with
`cfo.data.load.apply_event` and then passes it to the registered subscribers (T011 wires `Agent.handle`).

Sim time only advances while someone calls `tick()` (the worker loop does, every fraction of a second). Real time
comes from an injectable `clock` (epoch seconds), so tests use a fake one and never sleep.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from cfo.data.load import apply_event
from cfo.db.repo import Repository
from cfo.domain import Event

log = logging.getLogger(__name__)

# First simulated moment: the day the December replay starts.
DEFAULT_START = datetime(2025, 12, 1, tzinfo=UTC)
# Session-level advisory lock key ("sim" in ASCII): one process advances the replay at a time.
LOCK_KEY = 0x73696D

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sim_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    sim_time    TIMESTAMPTZ NOT NULL,
    paused      BOOLEAN NOT NULL DEFAULT TRUE,
    speed       DOUBLE PRECISION NOT NULL,
    last_tick   DOUBLE PRECISION NOT NULL,  -- real time (epoch seconds) up to which sim_time is accounted for
    next_index  INTEGER NOT NULL DEFAULT 0  -- index into the sorted event list of the next event to emit
)
"""

Subscriber = Callable[[Event], None]

# Process-wide subscribers, used by the plain functions in `cfo.sim.control`.
SUBSCRIBERS: list[Subscriber] = []


def subscribe(callback: Subscriber) -> Subscriber:
    """Register a callback that receives every event right after it was written (also usable as decorator)."""
    SUBSCRIBERS.append(callback)
    return callback


def unsubscribe(callback: Subscriber) -> None:
    if callback in SUBSCRIBERS:
        SUBSCRIBERS.remove(callback)


class SimStatus(BaseModel):
    sim_time: datetime  # current simulated time (live: includes the time elapsed since the last tick)
    paused: bool
    speed: float  # sim-seconds per real second
    emitted: int  # events written so far
    total: int
    next_event_ts: datetime | None  # None when everything was replayed
    finished: bool


@dataclass
class _State:
    sim_time: datetime
    paused: bool
    speed: float
    last_tick: float
    next_index: int


def _check_speed(speed: float) -> float:
    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not math.isfinite(speed) or speed <= 0:
        raise ValueError("speed must be a positive, finite number")
    return float(speed)


def parse_when(value: str | date | datetime) -> datetime:
    """`YYYY-MM-DD` means that day at 00:00 UTC; a naive datetime is taken as UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class Simulator:
    def __init__(
        self,
        repo: Repository,
        events: Sequence[Event],
        *,
        speed: float = 1.0,
        start: datetime = DEFAULT_START,
        clock: Callable[[], float] = time.time,
        subscribers: list[Subscriber] | None = None,
    ):
        self.repo = repo
        self.events = sorted(events, key=lambda e: e.ts)  # stable: file order wins for equal timestamps
        self.default_speed = speed
        self.start_time = start
        self.clock = clock
        self.subscribers = SUBSCRIBERS if subscribers is None else subscribers
        repo.conn.execute(SCHEMA_SQL)
        repo.conn.commit()

    # -- state storage --------------------------------------------------------------------------------------
    def _load(self) -> _State:
        row = self.repo.conn.execute("SELECT sim_time, paused, speed, last_tick, next_index FROM sim_state").fetchone()
        self.repo.conn.commit()  # no transaction left open between calls
        if row is None:
            self.repo.conn.execute(
                "INSERT INTO sim_state (id, sim_time, paused, speed, last_tick, next_index) VALUES (1, %s, TRUE, %s, %s, 0)"
                " ON CONFLICT DO NOTHING",
                (self.start_time, self.default_speed, self.clock()),
            )
            self.repo.conn.commit()
            return self._load()
        return _State(**row)

    def _save(self, st: _State) -> None:
        self.repo.conn.execute(
            "UPDATE sim_state SET sim_time=%s, paused=%s, speed=%s, last_tick=%s, next_index=%s WHERE id=1",
            (st.sim_time, st.paused, st.speed, st.last_tick, st.next_index),
        )
        self.repo.conn.commit()

    def _locked(self) -> _Lock:
        return _Lock(self.repo)

    # -- core: move sim time forward ------------------------------------------------------------------------
    def _emit_through(self, st: _State, until: datetime, out: list[Event]) -> None:
        """Write all events with ts <= `until` (in order), persisting the cursor after each one."""
        while st.next_index < len(self.events) and self.events[st.next_index].ts <= until:
            self._emit_next(st, out)

    def _emit_next(self, st: _State, out: list[Event]) -> None:
        """Write exactly the next event (equal timestamps are separate steps) and persist the cursor."""
        event = self.events[st.next_index]
        apply_event(self.repo, event)
        st.next_index += 1
        st.sim_time = max(st.sim_time, event.ts)
        self._save(st)
        out.append(event)

    def _elapse(self, st: _State, now: float, out: list[Event], *, emit: bool = True) -> None:
        """Account for the real time since `last_tick` (only counts while running).

        With `emit=False` the clock moves but overdue events stay queued for the next `tick`."""
        if not st.paused:
            horizon = self.events[-1].ts if self.events else st.sim_time
            horizon = max(horizon, st.sim_time)
            try:
                target = min(st.sim_time + timedelta(seconds=max(0.0, now - st.last_tick) * st.speed), horizon)
            except OverflowError:
                target = horizon
            if emit:
                self._emit_through(st, target, out)
            st.sim_time = max(st.sim_time, target)
            if st.next_index >= len(self.events):
                st.paused = True  # nothing left to replay
        st.last_tick = now
        self._save(st)

    def _dispatch(self, events: list[Event]) -> None:
        for event in events:
            for callback in list(self.subscribers):
                try:
                    callback(event)
                except Exception:  # a broken subscriber must not stop the clock
                    log.exception("simulator subscriber failed on event %s", event.id)

    def _run_locked(self, mutate: Callable[[_State, float, list[Event]], Any] | None = None,
                    *, flush: bool = True) -> tuple[Any, list[Event]]:
        out: list[Event] = []
        with self._locked():
            st = self._load()
            now = self.clock()
            self._elapse(st, now, out, emit=flush)
            result = mutate(st, now, out) if mutate else None
            self._save(st)
        self._dispatch(out)
        return result, out

    # -- control surface ------------------------------------------------------------------------------------
    def tick(self) -> list[Event]:
        """Advance by the real time elapsed since the last call; returns the events written."""
        return self._run_locked()[1]

    def start(self, speed: float | None = None) -> SimStatus:
        # Validate before any side effect (the lock flushes overdue events, the first load stores the default).
        if speed is not None:
            speed = _check_speed(speed)
        else:
            _check_speed(self.default_speed)
            _check_speed(self._load().speed)

        def mutate(st: _State, now: float, out: list[Event]) -> None:
            if speed is not None:
                st.speed = speed
            st.paused = False
            st.last_tick = now

        self._run_locked(mutate)
        return self.status()

    def pause(self) -> SimStatus:
        def mutate(st: _State, now: float, out: list[Event]) -> None:
            st.paused = True

        self._run_locked(mutate)
        return self.status()

    def set_speed(self, speed: float) -> SimStatus:
        speed = _check_speed(speed)

        def mutate(st: _State, now: float, out: list[Event]) -> None:
            st.speed = speed

        self._run_locked(mutate)
        return self.status()

    def step(self) -> Event | None:
        """Emit exactly one event right now (sim time jumps to its timestamp); None if nothing is left.

        Ties at the same timestamp are separate steps. On a running playback the clock keeps its elapsed time but
        overdue events are not flushed here: the next `tick` delivers them."""

        def mutate(st: _State, now: float, out: list[Event]) -> None:
            if st.next_index < len(self.events):
                self._emit_next(st, out)
                if not st.paused and st.next_index >= len(self.events):
                    st.paused = True  # nothing left to replay

        _, out = self._run_locked(mutate, flush=False)
        return out[-1] if out else None

    def jump(self, to: str | date | datetime) -> list[Event]:
        """Move sim time forward to `to` (a date means its 00:00 UTC), writing every event in between."""
        target = parse_when(to)

        def mutate(st: _State, now: float, out: list[Event]) -> None:
            if target < st.sim_time:
                raise ValueError(f"cannot jump back: it is already {st.sim_time.isoformat()} (use reset)")
            self._emit_through(st, target, out)
            st.sim_time = target

        return self._run_locked(mutate)[1]

    def reset(self) -> SimStatus:
        """Back to the beginning, paused. Does not touch the events table (use `cfo.data.load --reset`)."""
        with self._locked():
            self._load()
            self._save(_State(self.start_time, True, self._load().speed, self.clock(), 0))
        return self.status()

    def status(self) -> SimStatus:
        st = self._load()
        sim_time = st.sim_time
        if not st.paused:
            horizon = max(self.events[-1].ts if self.events else sim_time, sim_time)
            try:
                sim_time = min(sim_time + timedelta(seconds=max(0.0, self.clock() - st.last_tick) * st.speed), horizon)
            except OverflowError:
                sim_time = horizon
        nxt = self.events[st.next_index].ts if st.next_index < len(self.events) else None
        return SimStatus(sim_time=sim_time, paused=st.paused, speed=st.speed, emitted=st.next_index,
                         total=len(self.events), next_event_ts=nxt, finished=nxt is None)

    # -- worker loop ----------------------------------------------------------------------------------------
    def run(self, stop: threading.Event, *, max_wait: float = 1.0, wait: Callable[[float], Any] | None = None) -> None:
        """Tick until `stop` is set. Waits until the next event is due (at most `max_wait` real seconds).

        `wait(seconds)` defaults to `stop.wait`; tests pass a function that advances a fake clock.
        """
        wait = wait or stop.wait
        while not stop.is_set():
            self.tick()
            st = self.status()
            delay = max_wait
            if not st.paused and st.next_event_ts is not None:
                delay = min(max_wait, max(0.01, (st.next_event_ts - st.sim_time).total_seconds() / st.speed))
            wait(delay)


class _Lock:
    """Session-level advisory lock on the repository's connection: survives the commits inside apply_event."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def __enter__(self) -> None:
        self.repo.conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))

    def __exit__(self, *exc: object) -> None:
        self.repo.conn.rollback()
        self.repo.conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        self.repo.conn.commit()
