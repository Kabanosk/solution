"""Plain functions for the API (T020) and the CLI: each opens its own connection and acts on the shared state.

Events written by `step`/`jump` go to the subscribers registered in *this* process (`cfo.sim.subscribe`), so the
process that should run the agent must be the one that calls them (or runs `Simulator.run`).
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from cfo.config import get_settings
from cfo.data.load import load_events
from cfo.db.migrate import apply_schema
from cfo.db.repo import Repository
from cfo.domain import Event
from cfo.sim.engine import SimStatus, Simulator

DEFAULT_DATA_DIR = Path("data/generated")


def make_simulator(data_dir: Path | None = None, database_url: str | None = None) -> Simulator:
    """Simulator on a fresh connection, events from `<data_dir>/events/december.jsonl`, speed from SIM_SPEED."""
    repo = Repository.connect(database_url)
    return Simulator(repo, load_events(data_dir or DEFAULT_DATA_DIR), speed=get_settings().sim_speed)


def _with_sim(data_dir: Path | None, fn):
    sim = make_simulator(data_dir)
    try:
        return fn(sim)
    finally:
        sim.repo.conn.close()


def start(speed: float | None = None, data_dir: Path | None = None) -> SimStatus:
    return _with_sim(data_dir, lambda s: s.start(speed))


def pause(data_dir: Path | None = None) -> SimStatus:
    return _with_sim(data_dir, lambda s: s.pause())


def step(data_dir: Path | None = None) -> Event | None:
    return _with_sim(data_dir, lambda s: s.step())


def jump(to: str | date | datetime, data_dir: Path | None = None) -> list[Event]:
    return _with_sim(data_dir, lambda s: s.jump(to))


def reset(data_dir: Path | None = None) -> SimStatus:
    return _with_sim(data_dir, lambda s: s.reset())


def status(data_dir: Path | None = None) -> SimStatus:
    return _with_sim(data_dir, lambda s: s.status())


def ensure_schema() -> None:
    apply_schema(get_settings().database_url)
