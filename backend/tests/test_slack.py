"""Slack adapter tests: a fake Web API client, no network, no Socket Mode connection."""

from __future__ import annotations

import json

import pytest
from cfo.channels.base import (SIMULATED, Action, ActionKind, ApprovalAnswer,
                               ChatMessage, ChatReply, Notifier,
                               approval_actions)
from cfo.channels.slack import SlackNotifier, parse_user_map
from cfo.domain import Employee, PolicyLevel
from slack_bolt.request import BoltRequest
from slack_sdk import WebClient
from slack_sdk.web import SlackResponse

CEO = Employee(id="vincenzo", name="Vincenzo", role="CEO", slack_user_id="U_CEO")
GRAZYNA = Employee(
    id="grazyna",
    name="Grazyna",
    role="Office manager",
    manager_id="vincenzo",
    slack_user_id="U_GRA",
)
NOBODY = Employee(id="nobody", name="No Slack", role="Intern")


def _response(data: dict) -> SlackResponse:
    return SlackResponse(
        client=None,
        http_verb="POST",
        api_url="",
        req_args={},
        data=data,
        headers={},
        status_code=200,
    )


class FakeSlackClient(WebClient):
    """Records Web API calls instead of sending them."""

    def __init__(self) -> None:
        super().__init__(token="xoxb-fake")
        self.posted: list[dict] = []
        self.updated: list[dict] = []
        self.ephemeral: list[dict] = []

    def auth_test(self, **kwargs):
        return _response(
            {
                "ok": True,
                "user_id": "U_BOT",
                "bot_id": "B_BOT",
                "team_id": "T1",
                "user": "cfo",
            }
        )

    def chat_postMessage(self, **kwargs):
        self.posted.append(kwargs)
        return _response(
            {
                "ok": True,
                "channel": "D_" + kwargs["channel"],
                "ts": f"100.{len(self.posted):03d}",
            }
        )

    def chat_update(self, **kwargs):
        self.updated.append(kwargs)
        return _response({"ok": True})

    def chat_postEphemeral(self, **kwargs):
        self.ephemeral.append(kwargs)
        return _response({"ok": True})


@pytest.fixture
def client() -> FakeSlackClient:
    return FakeSlackClient()


@pytest.fixture
def notifier(client) -> SlackNotifier:
    return SlackNotifier(
        [CEO, GRAZYNA, NOBODY], client=client, process_before_response=True
    )


def _blocks_text(message: dict) -> str:
    return json.dumps(message["blocks"])


def _dispatch(notifier: SlackNotifier, body: dict):
    """Feed a payload through the real Bolt app as Socket Mode would."""
    return notifier.app.dispatch(BoltRequest(body=body, mode="socket_mode"))


def _message_event(user: str, text: str, **extra) -> dict:
    event = {
        "type": "message",
        "channel_type": "im",
        "channel": "D1",
        "user": user,
        "text": text,
        "ts": "200.1",
    }
    event.update(extra)
    return {"type": "event_callback", "team_id": "T1", "event": event}


def _click(
    user: str, action_id: str, value: str, channel: str = "D_U_CEO", ts: str = "100.001"
) -> dict:
    return {
        "type": "block_actions",
        "team": {"id": "T1"},
        "user": {"id": user, "team_id": "T1"},
        "channel": {"id": channel},
        "container": {"type": "message", "channel_id": channel, "message_ts": ts},
        "message": {
            "ts": ts,
            "text": "x",
            "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "orig"}}],
        },
        "actions": [{"type": "button", "action_id": action_id, "value": value}],
    }


def test_implements_notifier_protocol(notifier):
    assert isinstance(notifier, Notifier)


def test_parse_user_map():
    assert parse_user_map("vincenzo=U1, grazyna = U2,,") == {
        "vincenzo": "U1",
        "grazyna": "U2",
    }
    assert parse_user_map("") == {}
    with pytest.raises(ValueError):
        parse_user_map("vincenzo")


@pytest.mark.parametrize(
    "level", [PolicyLevel.AUTO, PolicyLevel.NOTIFY, PolicyLevel.APPROVAL, SIMULATED]
)
def test_send_dm_labels_every_level(notifier, client, level):
    notifier.send_dm(GRAZYNA, "Took invoice AS-69664 from your Downloads folder", level)
    msg = client.posted[0]
    assert msg["channel"] == "U_GRA"
    assert msg["text"].startswith(f"[{level}]")  # notification / fallback text
    assert f"[{level}]" in _blocks_text(msg)
    assert "AS-69664" in _blocks_text(msg)
    assert not any(b["type"] == "actions" for b in msg["blocks"])


def test_send_dm_returns_channel_and_ts_ref(notifier):
    ref = notifier.send_dm(CEO, "hello", PolicyLevel.NOTIFY)
    assert ref == "D_U_CEO:100.001"


def test_send_dm_escapes_slack_control_characters(notifier, client):
    notifier.send_dm(CEO, "R&D <!channel> costs", PolicyLevel.NOTIFY)
    assert "&amp;" in _blocks_text(client.posted[0])
    assert "<!channel>" not in _blocks_text(client.posted[0])


def test_send_dm_with_approval_buttons(notifier, client):
    notifier.send_dm(
        CEO,
        "Request a EUR 30k credit line?",
        PolicyLevel.APPROVAL,
        approval_actions("apr_1"),
    )
    actions = next(b for b in client.posted[0]["blocks"] if b["type"] == "actions")
    buttons = {e["action_id"]: e for e in actions["elements"]}
    assert set(buttons) == {"cfo_approve", "cfo_reject"}
    assert all(e["value"] == "apr_1" for e in buttons.values())
    assert buttons["cfo_approve"]["text"]["text"] == "Approve"
    assert buttons["cfo_approve"]["style"] == "primary"
    assert buttons["cfo_reject"]["style"] == "danger"


def test_custom_button_label(notifier, client):
    notifier.send_dm(
        CEO,
        "q",
        PolicyLevel.APPROVAL,
        [Action(kind=ActionKind.APPROVE, request_id="a", label="Sign it")],
    )
    actions = next(b for b in client.posted[0]["blocks"] if b["type"] == "actions")
    assert actions["elements"][0]["text"]["text"] == "Sign it"


def test_send_dm_unmapped_employee_raises(notifier):
    with pytest.raises(ValueError, match="nobody"):
        notifier.send_dm(NOBODY, "hi", PolicyLevel.AUTO)


def test_env_user_map_overrides_and_fills_employee_slack_ids(client):
    n = SlackNotifier(
        [CEO, NOBODY],
        client=client,
        user_map={"nobody": "U_NEW", "vincenzo": "U_OTHER"},
    )
    n.send_dm(NOBODY, "hi", PolicyLevel.AUTO)
    n.send_dm(CEO, "hi", PolicyLevel.AUTO)
    assert [m["channel"] for m in client.posted] == ["U_NEW", "U_OTHER"]


def test_approve_click_calls_handler_and_replaces_buttons(notifier, client):
    answers: list[ApprovalAnswer] = []
    notifier.on_approval(answers.append)
    response = _dispatch(notifier, _click("U_CEO", "cfo_approve", "apr_1"))
    assert response.status == 200
    assert answers == [
        ApprovalAnswer(
            request_id="apr_1",
            approved=True,
            employee_id="vincenzo",
            message_ref="D_U_CEO:100.001",
        )
    ]
    update = client.updated[0]
    assert update["channel"] == "D_U_CEO" and update["ts"] == "100.001"
    assert not any(b["type"] == "actions" for b in update["blocks"])
    assert "Approved" in _blocks_text(update) and "Vincenzo" in _blocks_text(update)
    assert "orig" in _blocks_text(update)  # the question stays visible


def test_reject_click(notifier, client):
    answers: list[ApprovalAnswer] = []
    notifier.on_approval(answers.append)
    _dispatch(notifier, _click("U_CEO", "cfo_reject", "apr_2"))
    assert answers[0].approved is False
    assert "Rejected" in _blocks_text(client.updated[0])


def test_click_from_unknown_user_is_ignored(notifier, client):
    answers: list[ApprovalAnswer] = []
    notifier.on_approval(answers.append)
    response = _dispatch(notifier, _click("U_STRANGER", "cfo_approve", "apr_1"))
    assert response.status == 200
    assert answers == [] and client.updated == []
    assert client.ephemeral and client.ephemeral[0]["user"] == "U_STRANGER"


def test_click_without_handler_does_not_crash(notifier, client):
    assert _dispatch(notifier, _click("U_CEO", "cfo_approve", "apr_1")).status == 200
    assert client.updated == []


def test_handler_error_is_reported_not_raised(notifier, client):
    def boom(answer):
        raise RuntimeError("graph down")

    notifier.on_approval(boom)
    assert _dispatch(notifier, _click("U_CEO", "cfo_approve", "apr_1")).status == 200
    assert client.updated == []  # buttons stay so the click can be retried
    assert "graph down" not in json.dumps(
        client.ephemeral
    )  # no internals leaked to the user
    assert client.ephemeral


def test_chat_message_routed_and_replied(notifier, client):
    got: list[ChatMessage] = []

    def handler(msg: ChatMessage):
        got.append(msg)
        return "Saved the rule."

    notifier.on_chat(handler)
    assert (
        _dispatch(
            notifier, _message_event("U_CEO", "equipment: I always approve myself")
        ).status
        == 200
    )
    assert got == [
        ChatMessage(
            employee_id="vincenzo",
            text="equipment: I always approve myself",
            message_ref="D1:200.1",
        )
    ]
    reply = client.posted[0]
    assert reply["channel"] == "D1"
    assert "[AUTO]" in reply["text"] and "Saved the rule." in _blocks_text(reply)


def test_chat_reply_with_level(notifier, client):
    notifier.on_chat(lambda msg: ChatReply(text="Paid, simulated.", level=SIMULATED))
    _dispatch(notifier, _message_event("U_CEO", "pay it"))
    assert client.posted[0]["text"].startswith("[SIMULATED]")


def test_chat_handler_returning_none_stays_silent(notifier, client):
    notifier.on_chat(lambda msg: None)
    _dispatch(notifier, _message_event("U_CEO", "thanks"))
    assert client.posted == []


def test_chat_ignores_bots_edits_and_non_dm(notifier, client):
    got: list[ChatMessage] = []
    notifier.on_chat(lambda m: got.append(m) or "x")
    _dispatch(notifier, _message_event("U_BOT", "my own echo", bot_id="B_BOT"))
    _dispatch(notifier, _message_event("U_CEO", "edited", subtype="message_changed"))
    _dispatch(notifier, _message_event("U_CEO", "in a channel", channel_type="channel"))
    assert got == [] and client.posted == []


def test_chat_from_unknown_user_is_not_routed(notifier, client):
    got: list[ChatMessage] = []
    notifier.on_chat(lambda m: got.append(m) or "x")
    _dispatch(notifier, _message_event("U_STRANGER", "hello"))
    assert got == []
    assert (
        client.posted and "[NOTIFY]" in client.posted[0]["text"]
    )  # polite refusal, still labelled


def test_chat_handler_error_gets_a_generic_reply(notifier, client):
    def boom(msg):
        raise RuntimeError("secret internals")

    notifier.on_chat(boom)
    _dispatch(notifier, _message_event("U_CEO", "hi"))
    assert client.posted and "secret internals" not in json.dumps(client.posted)


def test_app_mention_is_routed_in_thread_without_mention_text(notifier, client):
    got: list[ChatMessage] = []
    notifier.on_chat(lambda m: got.append(m) or "ok")
    body = {
        "type": "event_callback",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "channel": "C9",
            "user": "U_CEO",
            "text": "<@U_BOT> what is the balance?",
            "ts": "300.1",
        },
    }
    _dispatch(notifier, body)
    assert got[0].text == "what is the balance?"
    assert (
        client.posted[0]["channel"] == "C9" and client.posted[0]["thread_ts"] == "300.1"
    )


def test_start_and_stop_use_socket_mode_handler(client):
    started: list[str] = []

    class FakeHandler:
        def __init__(self, app, app_token):
            started.append(app_token)

        def connect(self):
            started.append("connect")

        def close(self):
            started.append("close")

    n = SlackNotifier(
        [CEO], client=client, app_token="xapp-1", socket_handler_factory=FakeHandler
    )
    n.start()
    n.stop()
    assert started == ["xapp-1", "connect", "close"]


def test_start_without_app_token_fails_clearly(client):
    with pytest.raises(RuntimeError, match="SLACK_APP_TOKEN"):
        SlackNotifier([CEO], client=client).start()
