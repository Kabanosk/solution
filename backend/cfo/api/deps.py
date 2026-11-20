"""Shared route dependencies and query parameter types."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

from cfo.api.errors import ApiError
from cfo.db.repo import Repository
from cfo.domain import Event
from cfo.sim import control as sim_control
from cfo.sim.engine import SimStatus
from fastapi import Query, Response

LimitParam = Annotated[int, Query(ge=1, le=200, description="Page size.")]
OffsetParam = Annotated[int, Query(ge=0, description="Number of items to skip.")]


def mark_stub(response: Response) -> None:
    """Router dependency: flags stub data in the `X-Stub` header. Remove it when a route serves real data."""
    response.headers["X-Stub"] = "true"


def get_repo() -> Iterator[Repository]:
    """One database connection per request. Tests replace it with `app.dependency_overrides[get_repo]`."""
    with Repository.connect() as repo:
        yield repo


# --- agent (approvals) ---------------------------------------------------------------------------------


class ApprovalResolver(Protocol):
    """The part of `cfo.agent.graph.Agent` the API needs."""

    def resume_approval(self, approval_id: str, approved: bool, by: str) -> Any: ...


_agent: ApprovalResolver | None = None


def set_agent(agent: ApprovalResolver | None) -> None:
    """The process that builds the agent (`cfo.run`, T011) registers it here before serving requests."""
    global _agent
    _agent = agent


def get_agent() -> ApprovalResolver:
    """The agent that resumes approvals. Tests replace it with `app.dependency_overrides[get_agent]`."""
    if _agent is None:
        raise ApiError(
            503,
            "agent_unavailable",
            "No agent is running in this API process, approvals cannot be answered.",
        )
    return _agent


# --- simulator control -----------------------------------------------------------------------------------


class SimControl(Protocol):
    """`cfo.sim.control` (module of plain functions) or a fake with the same four functions."""

    def start(self, speed: float | None = None) -> SimStatus: ...
    def pause(self) -> SimStatus: ...
    def step(self) -> Event | None: ...
    def status(self) -> SimStatus: ...


class _ModuleControl:
    """Adapts the module functions of `cfo.sim.control` (each on its own connection) to `SimControl`."""

    def start(self, speed: float | None = None) -> SimStatus:
        return sim_control.start(speed)

    def pause(self) -> SimStatus:
        return sim_control.pause()

    def step(self) -> Event | None:
        return sim_control.step()

    def status(self) -> SimStatus:
        return sim_control.status()


def get_sim_control() -> SimControl:
    """Tests replace it with `app.dependency_overrides[get_sim_control]`."""
    return _ModuleControl()


# --- live event stream ----------------------------------------------------------------------------------


@dataclass
class StreamConfig:
    """How `GET /v1/events` polls the database. Tests inject `wait` (no real sleeping) and `max_polls`."""

    repo_factory: Callable[[], AbstractContextManager[Repository]] = Repository.connect
    interval: float = 1.0  # seconds between polls
    wait: Callable[[float], None] = (
        time.sleep
    )  # called with `interval` between two polls
    keepalive_polls: int = (
        15  # a `: keepalive` comment after this many polls without news
    )
    batch: int = 100  # at most this many rows per table and poll
    max_polls: int | None = None  # None = until the client disconnects


def get_stream_config() -> StreamConfig:
    return StreamConfig()
