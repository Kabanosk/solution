"""Bounded, factual action logs for decision-backed dashboard jobs.

Logs live in decisions.payload.job_log. The checkpoint carries the history until
a node creates a decision, and across approval interrupts and resumes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from langgraph.errors import GraphBubbleUp

log = logging.getLogger(__name__)
MAX_ENTRIES = 500
MAX_MESSAGE_CHARS = 2000


def recorded_node(name: str, fn: Callable, repo_factory: Callable) -> Callable:
    """Record completed actions, not prompts, document bodies or model reasoning."""

    def persist(state: dict, output: dict, entries: list[dict[str, str]]) -> None:
        ids = list(dict.fromkeys([*state.get("decision_ids", []), *output.get("decision_ids", [])]))
        if not ids:
            return
        history = [*state.get("job_log", []), *entries][-MAX_ENTRIES:]
        try:
            with repo_factory() as repo:
                repo.set_decision_job_log(ids, history)
        except Exception:  # logging must not replay or prevent a business action
            log.warning("Could not persist agent job log")

    @wraps(fn)
    def run(state: dict[str, Any]) -> dict[str, Any]:
        try:
            output = fn(state)
        except GraphBubbleUp:
            # Approval interrupts are control flow, not failed jobs. The request
            # node has already persisted its waiting-for-approval message.
            raise
        except Exception:
            persist(state, {}, [entry("error", f"{name.replace('_', ' ')} failed. See agent service logs for details.")])
            raise
        messages = output.get("steps") or [f"{name.replace('_', ' ')} completed"]
        entries = [entry("info", str(message)) for message in messages]
        persist(state, output, entries)
        return {**output, "job_log": entries}

    def entry(level: str, message: str) -> dict[str, str]:
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level,
            "source": name,
            "message": message[:MAX_MESSAGE_CHARS],
        }

    return run
