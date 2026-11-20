"""Simulator: replay on a fake clock (no sleeping), pause/step/jump, shared state, CLI, December data."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from cfo.config import get_settings
from cfo.data import load
from cfo.db.migrate import SCHEMA_PATH
from cfo.db.repo import Repository
from cfo.domain import Event, EventKind
from cfo.sim import __main__ as cli
from cfo.sim import control
from cfo.sim.engine import DEFAULT_START, Simulator, parse_when

SCHEMA = f"t010_test_{os.getpid()}"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def txn_event(n: int, ts: datetime) -> Event:
    return Event(
        id=f"evt_t{n}",
        ts=ts,
        kind=EventKind.BANK_TRANSACTION,
        payload={
            "transaction": {
                "id": f"txn_t{n}",
                "booking_date": ts.date().isoformat(),
                "type": "SUPPLIER PAYMENT",
                "reference": f"Invoice AS-{n}",
                "counterparty": "Acme",
                "amount": "-10.00",
            }
        },
    )


def make_events() -> list[Event]:
    d = lambda day, hour: datetime(2025, 12, day, hour, tzinfo=UTC)
    # deliberately unsorted: the simulator sorts by timestamp
    return [
        txn_event(3, d(2, 12)),
        txn_event(1, d(1, 8)),
        txn_event(2, d(1, 9)),
        txn_event(4, d(5, 8)),
    ]


@pytest.fixture
def db_url():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
    # every connection of this test lives in the isolated schema (never public)
    isolated = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{SCHEMA}"
    with psycopg.connect(isolated) as c:
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    yield isolated
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sim(db_url, clock):
    repo = Repository.connect(db_url)
    got: list[Event] = []
    s = Simulator(
        repo, make_events(), speed=3600.0, clock=clock, subscribers=[got.append]
    )
    s.got = got  # type: ignore[attr-defined]
    yield s
    repo.conn.close()


def stored_ids(sim: Simulator) -> list[str]:
    return [e.id for e in sim.repo.list_events()]


def test_starts_paused_and_clock_does_not_run(sim, clock):
    st = sim.status()
    assert (
        st.paused and st.sim_time == DEFAULT_START and st.emitted == 0 and st.total == 4
    )
    clock.advance(10_000)
    assert sim.tick() == [] and sim.status().sim_time == DEFAULT_START


def test_replay_at_speed(sim, clock):
    sim.start()  # 3600 sim-seconds per real second: one sim hour per real second
    clock.advance(1)  # 01.12 01:00
    assert sim.tick() == []
    clock.advance(7)  # 01.12 08:00 -> first event is due exactly now
    assert [e.id for e in sim.tick()] == ["evt_t1"]
    clock.advance(1)  # 09:00
    assert [e.id for e in sim.tick()] == ["evt_t2"]
    assert sim.status().sim_time == datetime(2025, 12, 1, 9, tzinfo=UTC)
    clock.advance(27)  # 02.12 12:00
    assert [e.id for e in sim.tick()] == ["evt_t3"]
    assert [e.id for e in sim.got] == [
        "evt_t1",
        "evt_t2",
        "evt_t3",
    ]  # subscriber saw them in order
    assert set(stored_ids(sim)) == {
        "evt_t1",
        "evt_t2",
        "evt_t3",
    }  # also logged in the events table
    assert (
        sim.repo.get_transaction("txn_t3") is not None
    )  # applied to the domain tables, not only logged


def test_no_double_emit_and_live_status(sim, clock):
    sim.start(speed=60)
    clock.advance(3600 * 8 / 60)  # 8 sim hours
    assert len(sim.tick()) == 1
    assert sim.tick() == []
    clock.advance(30)  # 30 s real = 30 sim minutes, not persisted yet
    assert sim.status().sim_time == datetime(2025, 12, 1, 8, 30, tzinfo=UTC)
    assert len(sim.got) == 1


def test_pause_freezes_time_and_resume_continues(sim, clock):
    sim.start()
    clock.advance(2)  # 02:00
    sim.pause()
    clock.advance(100_000)
    assert sim.tick() == []
    assert (
        sim.status().sim_time == datetime(2025, 12, 1, 2, tzinfo=UTC)
        and sim.status().paused
    )
    sim.start()
    clock.advance(6)  # 08:00
    assert [e.id for e in sim.tick()] == ["evt_t1"]


def test_pause_accounts_for_time_up_to_the_pause(sim, clock):
    sim.start()
    clock.advance(9)  # 09:00 not yet ticked
    sim.pause()  # events due before the pause are emitted, not lost
    assert [e.id for e in sim.got] == ["evt_t1", "evt_t2"]


def test_step_emits_next_event_and_moves_clock(sim):
    first = sim.step()
    assert first.id == "evt_t1" and sim.status().sim_time == first.ts
    assert sim.step().id == "evt_t2"
    assert [e.id for e in sim.got] == ["evt_t1", "evt_t2"]
    assert (
        sim.status().emitted == 2 and sim.status().paused
    )  # stepping does not start the clock
    sim.step()
    sim.step()
    assert sim.step() is None and sim.status().finished


def test_step_while_running_does_not_double_emit(sim, clock):
    sim.start()
    sim.step()  # sim time -> 01.12 08:00
    clock.advance(0.5)  # 08:30
    assert sim.tick() == []
    clock.advance(0.5)  # 09:00
    assert [e.id for e in sim.tick()] == ["evt_t2"]
    assert [e.id for e in sim.got] == ["evt_t1", "evt_t2"]


def test_jump_emits_in_between_and_sets_time(sim):
    emitted = sim.jump("2025-12-02")
    assert [e.id for e in emitted] == ["evt_t1", "evt_t2"]
    assert sim.status().sim_time == datetime(2025, 12, 2, tzinfo=UTC)
    assert sim.status().next_event_ts == datetime(2025, 12, 2, 12, tzinfo=UTC)
    assert sim.jump(date(2025, 12, 2)) == []  # same moment: nothing new
    with pytest.raises(ValueError):
        sim.jump("2025-12-01")
    assert [e.id for e in sim.jump("2026-01-01")] == ["evt_t3", "evt_t4"]
    assert sim.status().finished


def test_finishing_pauses_and_status_stays_in_range(sim, clock):
    sim.start(speed=10**9)
    clock.advance(10**6)
    assert len(sim.tick()) == 4
    st = sim.status()
    assert (
        st.paused
        and st.finished
        and st.sim_time == datetime(2025, 12, 5, 8, tzinfo=UTC)
    )


def test_state_is_shared_between_connections(sim, db_url, clock):
    sim.start(speed=120)
    other_repo = Repository.connect(db_url)
    other = Simulator(
        other_repo, make_events(), clock=clock
    )  # a second process (API) with its own connection
    try:
        st = other.status()
        assert not st.paused and st.speed == 120
        other.pause()
        assert sim.status().paused
        other.step()
        assert sim.status().emitted == 1
    finally:
        other_repo.conn.close()


def test_failing_subscriber_does_not_stop_the_replay(sim):
    sim.subscribers.insert(0, lambda e: 1 / 0)
    sim.jump("2025-12-03")
    assert len(sim.got) == 3 and sim.status().emitted == 3


def test_reset(sim):
    sim.jump("2025-12-03")
    sim.reset()
    st = sim.status()
    assert st.paused and st.emitted == 0 and st.sim_time == DEFAULT_START


def test_run_loop_with_fake_clock(sim, clock):
    stop = threading.Event()
    sim.start()

    def fake_wait(seconds: float) -> None:
        assert 0 < seconds <= 1.0
        clock.advance(seconds)
        if len(sim.got) == 3:
            stop.set()

    sim.run(stop, wait=fake_wait)
    assert [e.id for e in sim.got] == ["evt_t1", "evt_t2", "evt_t3"]


def test_parse_when():
    assert parse_when("2025-12-24") == datetime(2025, 12, 24, tzinfo=UTC)
    assert parse_when("2025-12-24T10:30:00+01:00").utcoffset() == timedelta(hours=1)
    assert parse_when(datetime(2025, 12, 1, 5)) == datetime(2025, 12, 1, 5, tzinfo=UTC)


# -- plain functions + CLI against a data dir ------------------------------------------------------------------
@pytest.fixture
def data_dir(tmp_path, monkeypatch, db_url) -> Path:
    (tmp_path / "events").mkdir()
    lines = [json.dumps(json.loads(e.model_dump_json())) for e in make_events()]
    (tmp_path / "events" / "december.jsonl").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    monkeypatch.setenv("DATABASE_URL", db_url)
    get_settings.cache_clear()
    monkeypatch.setattr(control, "ensure_schema", lambda: None)
    yield tmp_path
    get_settings.cache_clear()


def test_control_functions(data_dir):
    seen: list[Event] = []
    from cfo.sim.engine import subscribe, unsubscribe

    subscribe(seen.append)
    try:
        assert control.status(data_dir).paused
        assert control.step(data_dir).id == "evt_t1"
        assert [e.id for e in control.jump("2025-12-03", data_dir)] == [
            "evt_t2",
            "evt_t3",
        ]
        assert [e.id for e in seen] == ["evt_t1", "evt_t2", "evt_t3"]
        assert not control.start(2.0, data_dir).paused
        assert control.pause(data_dir).paused
        assert control.reset(data_dir).emitted == 0
    finally:
        unsubscribe(seen.append)


def test_cli(data_dir, capsys):
    base = ["--data", str(data_dir)]
    assert cli.main([*base, "status"]) == 0
    assert json.loads(capsys.readouterr().out)["emitted"] == 0
    assert cli.main([*base, "step"]) == 0
    assert "evt_t1" in capsys.readouterr().out
    assert cli.main([*base, "jump", "2025-12-03"]) == 0
    out = capsys.readouterr().out
    assert "emitted 2 events" in out
    assert cli.main([*base, "start", "--speed", "7200"]) == 0
    assert json.loads(capsys.readouterr().out)["speed"] == 7200
    assert cli.main([*base, "pause"]) == 0
    assert json.loads(capsys.readouterr().out)["paused"] is True


# -- December data from T002 ------------------------------------------------------------------------------------
def test_december_replay_from_generated_data(tmp_path, sim, clock):
    from cfo.data.generate import generate
    from cfo.data.source import SourceNotFound, find_source_csv

    try:
        find_source_csv()
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    out = tmp_path / "generated"
    generate(out, seed=42)
    load.seed(sim.repo, out)
    events = load.load_events(out)
    december = Simulator(sim.repo, events, speed=86400.0, clock=clock, subscribers=[])
    december.reset()
    december.start()
    clock.advance(40)  # 40 sim days at one day per second: the whole month
    emitted = december.tick()
    assert len(emitted) == len(events) > 100
    assert [e.id for e in emitted] == [e.id for e in sorted(events, key=lambda e: e.ts)]
    assert december.status().finished and len(sim.repo.list_events()) == len(events)
    pending = {
        t.invoice_number
        for t in sim.repo.list_transactions(since=date(2025, 12, 1))
        if not t.documented
    }
    assert {
        "AS-69664",
        "FLV-42026",
    } <= pending  # the demo's two missing documents arrived undocumented
