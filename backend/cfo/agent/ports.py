"""Ports the agent needs from the outside world.

`Notifier` is the minimal channel interface the agent depends on. The Slack implementation (T008) lives in
`cfo/channels/`; integration (T011) adapts it to this protocol. Tests use a fake.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from cfo.domain import PolicyLevel
from pydantic import BaseModel


class ActionButton(BaseModel):
    """A button under a DM. For approvals `action_id` is `approve` / `reject` and `value` the approval id."""

    action_id: str
    label: str
    value: str


@runtime_checkable
class Notifier(Protocol):
    def send_dm(
        self,
        employee_id: str,
        text: str,
        level: PolicyLevel,
        actions: Sequence[ActionButton] = (),
    ) -> str:
        """Send a direct message and return a channel reference (e.g. Slack channel + ts)."""
        ...
