"""Slack adapter of `Notifier`: slack_bolt in Socket Mode (no public URL, see docs/slack-setup.md).

* `send_dm` posts to the employee's Slack user id (the app opens the DM itself, scope `im:write`) as Block Kit:
  a section starting with the bold level label `[APPROVAL]`, and Approve / Reject buttons when asked.
* A button click is acknowledged at once, passed to the approval handler as an `ApprovalAnswer`, and then the
  buttons are replaced by "Approved/Rejected by <name>". If the handler raises, the buttons stay (retry possible).
* Direct messages and app mentions from known employees go to the chat handler; the reply is posted back,
  labelled too. Messages from bots, edits and strangers never reach the agent.
* The answer to an `app_mention` in a channel is never posted in the channel: it goes to the asker as a DM and
  the channel only gets an ephemeral "answered you in DM" that nobody else sees (financial data stays private).
* Every message the bot sends, including the ephemeral error / refusal notes, starts with a level label.

Employee <-> Slack user id: `Employee.slack_user_id` (set in the database or in code; the HR CSV `slack_id`
column is not read), overridden per employee by `SLACK_USER_MAP` (`vincenzo=U01ABC,grazyna=U02DEF`).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from slack_bolt import App
from slack_bolt.authorization import AuthorizeResult
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient

from cfo.channels.base import (
    Action,
    ActionKind,
    ApprovalAnswer,
    ApprovalHandler,
    ChatHandler,
    ChatMessage,
    ChatReply,
    Level,
    level_label,
)
from cfo.domain import Employee, PolicyLevel

log = logging.getLogger(__name__)

APPROVE_ACTION_ID = "cfo_approve"
REJECT_ACTION_ID = "cfo_reject"
_BUTTONS = {
    ActionKind.APPROVE: (APPROVE_ACTION_ID, "Approve", "primary"),
    ActionKind.REJECT: (REJECT_ACTION_ID, "Reject", "danger"),
}
_MAX_TEXT = 2900  # Slack section text limit is 3000 characters
_MENTION = re.compile(r"<@[A-Z0-9_]+>")


def parse_user_map(raw: str) -> dict[str, str]:
    """`"vincenzo=U01,grazyna=U02"` -> `{"vincenzo": "U01", "grazyna": "U02"}`."""
    result: dict[str, str] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        employee_id, sep, slack_id = part.partition("=")
        if not sep or not employee_id.strip() or not slack_id.strip():
            raise ValueError(f"bad SLACK_USER_MAP entry {part!r}, expected employee_id=SLACK_USER_ID")
        result[employee_id.strip()] = slack_id.strip()
    return result


def _escape(text: str) -> str:
    """Slack mrkdwn control characters; stops `<!channel>` and friends from firing."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _label_text(text: str, level: Level) -> str:
    return f"[{level_label(level)}] {text}"


def _section(text: str, level: Level) -> dict[str, Any]:
    body = _escape(text)
    if len(body) > _MAX_TEXT:
        body = body[: _MAX_TEXT - 1] + "…"
    return {"type": "section", "text": {"type": "mrkdwn", "text": f"*[{level_label(level)}]* {body}"}}


def _buttons(actions: Sequence[Action]) -> dict[str, Any]:
    elements = []
    for action in actions:
        action_id, default_label, style = _BUTTONS[action.kind]
        elements.append(
            {
                "type": "button",
                "action_id": action_id,
                "text": {"type": "plain_text", "text": action.label or default_label},
                "style": style,
                "value": action.request_id,
            }
        )
    return {"type": "actions", "elements": elements}


class SlackNotifier:
    """`Notifier` over Slack. Pass `client` (a `WebClient`) in tests; real use passes the two tokens.

    `process_before_response=True` runs handlers inside Bolt's dispatch (tests); by default they run on
    Bolt's worker threads so a slow agent never delays the 3-second Slack acknowledgement.
    """

    def __init__(
        self,
        employees: Iterable[Employee],
        *,
        bot_token: str = "",
        app_token: str = "",
        client: WebClient | None = None,
        user_map: Mapping[str, str] | None = None,
        process_before_response: bool = False,
        socket_handler_factory: Callable[[App, str], Any] = SocketModeHandler,
    ) -> None:
        overrides = dict(user_map or {})
        self._employees: dict[str, Employee] = {e.id: e for e in employees}
        self._slack_ids: dict[str, str] = {
            e.id: overrides.get(e.id) or e.slack_user_id for e in self._employees.values() if overrides.get(e.id) or e.slack_user_id
        }
        self._by_slack_id = {slack_id: self._employees[eid] for eid, slack_id in self._slack_ids.items()}
        self._app_token = app_token
        self._socket_handler_factory = socket_handler_factory
        self._socket: Any = None
        self._approval_handler: ApprovalHandler | None = None
        self._chat_handler: ChatHandler | None = None

        self._auth: AuthorizeResult | None = None
        self.app = App(
            client=client or WebClient(token=bot_token),
            authorize=self._make_authorize(),  # one workspace; auth.test runs lazily through our client
            request_verification_enabled=False,  # Socket Mode: Slack authenticates the websocket
            process_before_response=process_before_response,
        )
        self._client: WebClient = self.app.client
        self.app.action(APPROVE_ACTION_ID)(self._on_click)
        self.app.action(REJECT_ACTION_ID)(self._on_click)
        self.app.event("message")(self._on_message)
        self.app.event("app_mention")(self._on_mention)

    def _make_authorize(self) -> Callable[..., AuthorizeResult]:
        """Bolt matches `authorize` arguments by name, hence a closure instead of a bound method."""

        def authorize(enterprise_id: str | None, team_id: str | None) -> AuthorizeResult:
            if self._auth is None:
                info = self._client.auth_test()
                self._auth = AuthorizeResult(
                    enterprise_id=enterprise_id,
                    team_id=info.get("team_id") or team_id,
                    bot_token=self._client.token,
                    bot_id=info.get("bot_id"),
                    bot_user_id=info.get("user_id"),
                )
            return self._auth

        return authorize

    @classmethod
    def from_settings(cls, employees: Iterable[Employee], **kwargs: Any) -> SlackNotifier:
        """Tokens and `SLACK_USER_MAP` from `cfo.config.Settings` (env / `.env`)."""
        from cfo.config import get_settings

        settings = get_settings()
        return cls(
            employees,
            bot_token=settings.slack_bot_token,
            app_token=settings.slack_app_token,
            user_map=parse_user_map(settings.slack_user_map),
            **kwargs,
        )

    # --- Notifier -----------------------------------------------------------------------------

    def on_approval(self, handler: ApprovalHandler) -> None:
        self._approval_handler = handler

    def on_chat(self, handler: ChatHandler) -> None:
        self._chat_handler = handler

    def send_dm(self, employee: Employee, text: str, level: Level, actions: Sequence[Action] = ()) -> str:
        slack_id = self._slack_ids.get(employee.id)
        if not slack_id:
            raise ValueError(f"employee {employee.id!r} has no Slack user id (slack_user_id or SLACK_USER_MAP)")
        blocks = [_section(text, level)]
        if actions:
            blocks.append(_buttons(actions))
        return self._post(slack_id, text, level, blocks)

    def start(self) -> None:
        if not self._app_token:
            raise RuntimeError("SLACK_APP_TOKEN (xapp-...) is required for Socket Mode")
        self._socket = self._socket_handler_factory(self.app, self._app_token)
        self._socket.connect()

    def stop(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    # --- internals ----------------------------------------------------------------------------

    def _post(self, channel: str, text: str, level: Level, blocks: list[dict], thread_ts: str | None = None) -> str:
        kwargs: dict[str, Any] = {"channel": channel, "text": _label_text(text, level), "blocks": blocks}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        response = self._client.chat_postMessage(**kwargs)
        return f"{response['channel']}:{response['ts']}"

    def _ephemeral(self, channel: str, user: str, text: str) -> None:
        try:
            self._client.chat_postEphemeral(channel=channel, user=user, text=_label_text(text, PolicyLevel.NOTIFY))
        except Exception:  # noqa: BLE001 - best effort, never mask the original problem
            log.exception("could not send ephemeral message to %s", user)

    def _on_click(self, ack: Callable[[], None], body: dict[str, Any]) -> None:
        ack()
        click = body["actions"][0]
        channel = (body.get("channel") or {}).get("id") or body["container"]["channel_id"]
        message = body.get("message") or {}
        ts = message.get("ts") or body["container"]["message_ts"]
        slack_id = body["user"]["id"]
        employee = self._by_slack_id.get(slack_id)
        if employee is None:
            self._ephemeral(channel, slack_id, "You are not registered with the CFO agent, so your click was ignored.")
            return
        if self._approval_handler is None:
            log.warning("approval click from %s but no handler registered", employee.id)
            self._ephemeral(channel, slack_id, "The agent is not ready for answers yet. Please try again in a moment.")
            return
        approved = click["action_id"] == APPROVE_ACTION_ID
        answer = ApprovalAnswer(
            request_id=click["value"], approved=approved, employee_id=employee.id, message_ref=f"{channel}:{ts}"
        )
        try:
            self._approval_handler(answer)
        except Exception:  # noqa: BLE001
            log.exception("approval handler failed for request %s", answer.request_id)
            self._ephemeral(channel, slack_id, "Something went wrong while recording your answer. Please click again.")
            return
        verdict = "Approved" if approved else "Rejected"
        blocks = [b for b in message.get("blocks", []) if b.get("type") != "actions"]
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"*{verdict}* by {_escape(employee.name)}"}]})
        self._client.chat_update(
            channel=channel, ts=ts, text=f"{message.get('text', '')} ({verdict} by {employee.name})", blocks=blocks
        )

    def _on_message(self, event: dict[str, Any]) -> None:
        if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
            return
        self._route_chat(event, text=event.get("text", ""), thread_ts=None, private=False)

    def _on_mention(self, event: dict[str, Any]) -> None:
        if event.get("bot_id"):
            return
        text = _MENTION.sub("", event.get("text", "")).strip()
        self._route_chat(event, text=text, thread_ts=event.get("thread_ts") or event["ts"], private=True)

    def _route_chat(self, event: dict[str, Any], *, text: str, thread_ts: str | None, private: bool) -> None:
        """`private`: the event happened in a shared channel, so the answer goes to the asker's DM."""
        channel = event["channel"]
        employee = self._by_slack_id.get(event.get("user", ""))
        if employee is None:
            self._notice(event, thread_ts, "I only talk to people registered with the CFO agent.", private)
            return
        if self._chat_handler is None or not text:
            return
        message = ChatMessage(employee_id=employee.id, text=text, message_ref=f"{channel}:{event['ts']}")
        try:
            reply = self._chat_handler(message)
        except Exception:  # noqa: BLE001
            log.exception("chat handler failed for message from %s", employee.id)
            self._notice(event, thread_ts, "Something went wrong on my side. Please try again.", private)
            return
        if reply is None:
            return
        if isinstance(reply, str):
            reply = ChatReply(text=reply)
        if not private:
            self._reply(channel, thread_ts, reply.text, reply.level)
            return
        asker = event["user"]
        try:
            self._reply(asker, None, reply.text, reply.level)  # posting to a user id opens the DM
        except Exception:  # noqa: BLE001
            log.exception("could not DM the answer to %s", employee.id)
            self._ephemeral(channel, asker, "I could not send you a direct message. Please DM me instead.")
            return
        self._ephemeral(channel, asker, "I answered you in a DM.")

    def _notice(self, event: dict[str, Any], thread_ts: str | None, text: str, private: bool) -> None:
        """Refusal / error note: ephemeral (only the asker sees it) in a shared channel, a labelled reply in a DM."""
        if private:
            self._ephemeral(event["channel"], event["user"], text)
        else:
            self._reply(event["channel"], thread_ts, text, PolicyLevel.NOTIFY)

    def _reply(self, channel: str, thread_ts: str | None, text: str, level: Level) -> None:
        self._post(channel, text, level, [_section(text, level)], thread_ts=thread_ts)
