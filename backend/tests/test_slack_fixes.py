"""Codex review fixes for the Slack adapter (T043): real agent call shape, private answers, level labels."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from cfo.agent.ports import ActionButton
from cfo.agent.ports import Notifier as AgentNotifierPort
from cfo.channels.base import ChatMessage
from cfo.channels.slack import SlackNotifier
from cfo.domain import PolicyLevel
from cfo.run import AgentNotifier
from tests.test_slack import CEO, GRAZYNA, NOBODY, FakeSlackClient, _dispatch, _message_event, _click


class _FakeRepo:
    def list_employees(self):
        return [CEO, GRAZYNA, NOBODY]


@contextmanager
def _repo_factory():
    yield _FakeRepo()


@pytest.fixture
def client() -> FakeSlackClient:
    return FakeSlackClient()


@pytest.fixture
def notifier(client) -> SlackNotifier:
    return SlackNotifier([CEO, GRAZYNA, NOBODY], client=client, process_before_response=True)


def _mention(user: str, text: str = "<@U_BOT> what is the balance?", channel: str = "C9", ts: str = "300.1") -> dict:
    event = {"type": "app_mention", "channel": channel, "user": user, "text": text, "ts": ts}
    return {"type": "event_callback", "team_id": "T1", "event": event}


# --- (4) the call shape the agent really uses: employee id string + ActionButton ------------------------------
def test_agent_notifier_over_slack_takes_employee_id_strings(notifier, client):
    """`cfo/agent/graph.py` calls `notifier.send_dm(employee_id, text, level, [ActionButton...])`; `cfo/run.py`
    wires Slack behind `AgentNotifier`. Drive exactly that."""
    adapter = AgentNotifier(notifier, _repo_factory)
    assert isinstance(adapter, AgentNotifierPort)

    ref = adapter.send_dm(
        "vincenzo",
        "Request a EUR 30k credit line?",
        PolicyLevel.APPROVAL,
        [
            ActionButton(action_id="approve", label="Approve", value="apr_1"),
            ActionButton(action_id="reject", label="Reject", value="apr_1"),
        ],
    )
    assert ref == "D_U_CEO:100.001"
    msg = client.posted[0]
    assert msg["channel"] == "U_CEO"
    assert msg["text"].startswith("[APPROVAL]")
    actions = next(b for b in msg["blocks"] if b["type"] == "actions")
    assert {e["action_id"] for e in actions["elements"]} == {"cfo_approve", "cfo_reject"}
    assert all(e["value"] == "apr_1" for e in actions["elements"])


def test_agent_dm_swallows_unknown_employee_without_attribute_error(notifier):
    adapter = AgentNotifier(notifier, _repo_factory)
    with pytest.raises(ValueError, match="ghost"):
        adapter.send_dm("ghost", "hi", PolicyLevel.NOTIFY)


# --- (21) answers to an app_mention never land in the channel -------------------------------------------------
def test_mention_answer_goes_to_the_ceo_in_dm_not_the_channel(notifier, client):
    got: list[ChatMessage] = []
    notifier.on_chat(lambda m: got.append(m) or "Cash is EUR 412,000.")
    _dispatch(notifier, _mention("U_CEO"))
    assert got[0].text == "what is the balance?"

    answer = [m for m in client.posted if "412,000" in json.dumps(m)]
    assert len(answer) == 1
    assert answer[0]["channel"] == "U_CEO"  # DM, not C9
    assert "thread_ts" not in answer[0]
    assert answer[0]["text"].startswith("[AUTO]") or answer[0]["text"].startswith("[NOTIFY]")
    # nothing with the answer text anywhere in the channel
    assert not any(m["channel"] == "C9" for m in client.posted)


def test_mention_channel_only_sees_a_private_pointer(notifier, client):
    notifier.on_chat(lambda m: "Cash is EUR 412,000.")
    _dispatch(notifier, _mention("U_CEO"))
    assert "412,000" not in json.dumps(client.ephemeral)
    assert client.ephemeral, "the CEO is told in the channel (ephemeral) that the answer is in the DM"
    note = client.ephemeral[0]
    assert note["channel"] == "C9" and note["user"] == "U_CEO"
    assert "DM" in note["text"]
    assert note["text"].startswith("[")  # level label


def test_mention_silent_handler_posts_nothing(notifier, client):
    notifier.on_chat(lambda m: None)
    _dispatch(notifier, _mention("U_CEO"))
    assert client.posted == [] and client.ephemeral == []


def test_mention_handler_error_is_ephemeral_not_public(notifier, client):
    def boom(msg):
        raise RuntimeError("secret internals")

    notifier.on_chat(boom)
    _dispatch(notifier, _mention("U_CEO"))
    assert client.posted == []  # nothing public in the channel
    assert len(client.ephemeral) == 1
    note = client.ephemeral[0]
    assert note["channel"] == "C9" and note["user"] == "U_CEO"
    assert note["text"].startswith("[NOTIFY]") and "secret internals" not in note["text"]


def test_mention_from_unregistered_user_is_refused_ephemerally(notifier, client):
    got: list[ChatMessage] = []
    notifier.on_chat(lambda m: got.append(m) or "x")
    _dispatch(notifier, _mention("U_STRANGER"))
    assert got == [] and client.posted == []
    assert client.ephemeral[0]["user"] == "U_STRANGER" and client.ephemeral[0]["channel"] == "C9"
    assert client.ephemeral[0]["text"].startswith("[NOTIFY]")


def test_dm_chat_still_answers_in_the_dm(notifier, client):
    notifier.on_chat(lambda m: "ok")
    _dispatch(notifier, _message_event("U_CEO", "hi"))
    assert client.posted[0]["channel"] == "D1" and client.ephemeral == []


# --- (P3) every bot message carries a level label -------------------------------------------------------------
def test_approval_click_errors_are_labelled(notifier, client):
    # stranger, no handler, handler failure
    _dispatch(notifier, _click("U_STRANGER", "cfo_approve", "apr_1"))
    _dispatch(notifier, _click("U_CEO", "cfo_approve", "apr_1"))  # no handler registered yet

    def boom(answer):
        raise RuntimeError("graph down")

    notifier.on_approval(boom)
    _dispatch(notifier, _click("U_CEO", "cfo_approve", "apr_1"))
    assert len(client.ephemeral) == 3
    for message in client.ephemeral:
        assert message["text"].startswith("[NOTIFY]"), message["text"]
