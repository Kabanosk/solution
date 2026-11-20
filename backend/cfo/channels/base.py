"""Channel interface (D-007): how the agent talks to people, whatever the chat system behind it.

The agent depends only on `Notifier`. Three things cross the boundary:

* Out: `send_dm(employee, text, level, actions)` sends a labelled direct message and returns a message ref.
  With `approval_actions(request_id)` the message carries Approve / Reject buttons.
* In (approval): `on_approval(handler)`; the handler gets an `ApprovalAnswer` when someone clicks a button.
  The agent resumes its LangGraph interrupt for `answer.request_id` there and should check that
  `answer.employee_id` is the approver of that `ApprovalRequest`.
* In (chat): `on_chat(handler)`; free text from a known employee (the CEO talks to the agent) goes to the
  handler as a `ChatMessage`; whatever it returns is sent back as the reply.

Handlers are plain synchronous callables and may be called from worker threads. Every outgoing message
shows its level label (`AUTO`, `NOTIFY`, `APPROVAL` or `SIMULATED`, see `Level`). The message ref is an opaque
string: store it in `ApprovalRequest.channel_ref`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from cfo.domain import Employee, PolicyLevel
from pydantic import BaseModel

SIMULATED = (
    "SIMULATED"  # label for executed-but-not-real actions (D-005); not a policy level
)
Level = PolicyLevel | Literal["SIMULATED"]


def level_label(level: Level) -> str:
    """Upper-case label shown on a message, e.g. `APPROVAL`."""
    return str(level).upper()


class ActionKind(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class Action(BaseModel):
    """A button under a message. `request_id` is the `ApprovalRequest.id` the click answers."""

    kind: ActionKind
    request_id: str
    label: str | None = None  # default: "Approve" / "Reject"


def approval_actions(request_id: str) -> list[Action]:
    """The standard Approve / Reject pair for an `ApprovalRequest`."""
    return [
        Action(kind=ActionKind.APPROVE, request_id=request_id),
        Action(kind=ActionKind.REJECT, request_id=request_id),
    ]


class ApprovalAnswer(BaseModel):
    """A person clicked Approve or Reject."""

    request_id: str
    approved: bool
    employee_id: str  # who clicked (only known employees are passed on)
    message_ref: str  # the message that carried the buttons


class ChatMessage(BaseModel):
    """Free text from a known employee."""

    employee_id: str
    text: str
    message_ref: str  # the incoming message


class ChatReply(BaseModel):
    """Reply with an explicit level label; a plain `str` reply is labelled AUTO."""

    text: str
    level: Level = PolicyLevel.AUTO


ApprovalHandler = Callable[[ApprovalAnswer], None]
ChatHandler = Callable[[ChatMessage], "str | ChatReply | None"]  # None: stay silent


@runtime_checkable
class Notifier(Protocol):
    def send_dm(
        self,
        employee: Employee,
        text: str,
        level: Level,
        actions: Sequence[Action] = (),
    ) -> str:
        """DM `employee` with `text` labelled `level`, buttons under it if `actions`. Returns the message ref.

        Raises `ValueError` when the employee has no channel address (e.g. no `slack_user_id`).
        """
        ...

    def on_approval(self, handler: ApprovalHandler) -> None:
        """Register the (single) callback for Approve / Reject clicks; replaces any earlier one."""
        ...

    def on_chat(self, handler: ChatHandler) -> None:
        """Register the (single) callback for free-text messages; replaces any earlier one."""
        ...

    def start(self) -> None:
        """Connect and begin receiving (non-blocking). Sending works without it."""
        ...

    def stop(self) -> None:
        """Disconnect."""
        ...
