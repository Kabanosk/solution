"""Dashboard instructions are fetched on demand, never from company data."""
from __future__ import annotations

import json

import pytest

from scout.loops.toolbox import SPECS, Toolbox


def help_box():
    # A help lookup must work without database, connectors, task records or credentials.
    return Toolbox(ctx=None, requester="staff", role="staff", task=None)


@pytest.mark.parametrize(("topic", "href"), [
    ("getting_started", "/#chat"), ("chat", "/#chat"), ("overview", "/#overview"),
    ("tasks", "/#tasks"), ("transactions", "/#transactions"), ("connections", "/#connections"),
    ("audit", "/#audit"), ("policies", "/#policies"), ("llm_usage", "/#llm-usage"),
])
def test_help_returns_only_the_requested_page_with_bounded_context(topic, href):
    result = help_box().call("dashboard_help", {"topic": topic})
    assert "error" not in result
    assert result["href"] == href
    assert result["steps"] and len(result["steps"]) <= 4
    assert len(json.dumps(result)) <= 1600
    assert all(link["href"].startswith("/#") for link in result.get("related", []))


def test_help_defaults_to_starting_in_scout_chat_and_preserves_its_data():
    box = help_box()
    first = box.call("dashboard_help", {})
    assert first["href"] == "/#chat"
    first["steps"].clear()
    assert box.call("dashboard_help", {})["steps"]


@pytest.mark.parametrize("topic", ["does_not_exist", 42, ["tasks"]])
def test_unknown_or_invalid_topic_is_a_compact_recoverable_error(topic):
    result = help_box().call("dashboard_help", {"topic": topic})
    assert result["error"] == "unknown_dashboard_topic"
    assert "getting_started" in result["topics"] and "tasks" in result["topics"]
    assert len(json.dumps(result)) < 400


def test_connection_help_does_not_promise_disabled_authorization_or_sync():
    guide = help_box().call("dashboard_help", {"topic": "connections"})
    instructions = " ".join(guide["steps"]).lower()
    assert "unavailable" in instructions
    assert "google" in instructions and "ksef" in instructions
    assert "status" in instructions and "permissions" in instructions and "results" in instructions
    assert "starts authorization" not in instructions and "starts a test-environment invoice sync" not in instructions


def test_one_small_help_schema_is_available_to_the_model():
    specs = [spec for spec in SPECS if spec.name == "dashboard_help"]
    assert len(specs) == 1
    spec = specs[0]
    assert len(spec.description) < 200
    assert list(spec.parameters["properties"]) == ["topic"]
    assert spec.parameters["properties"]["topic"]["type"] == "string"
    assert len(json.dumps(spec.parameters)) + len(spec.description) < 650


def test_chat_api_looks_up_dashboard_help_then_streams_page_guidance(conn_factory, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from cfo.api.errors import install_error_handlers
    from cfo.llm import FakeLLM
    from scout import chat_api
    from scout.config import get_scout_settings
    from test_invoicing_loop import make_world

    requests = []

    def handler(messages, tools):
        requests.append([message.role for message in messages])
        if not any(message.role == "tool" for message in messages):
            assert any(spec.name == "dashboard_help" for spec in tools)
            return FakeLLM.tool_call("dashboard_help", topic="connections")
        guide = json.loads(next(message.content for message in messages if message.role == "tool"))
        assert guide["href"] == "/#connections" and "Google" in " ".join(guide["steps"])
        return "Open [Connections](/#connections) to review integration status. Expand its chevron for setup instructions."

    ctx = make_world(conn_factory, tmp_path, llm=FakeLLM(handler=handler))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.get_context_factory] = lambda: (lambda settings: ctx)
    app.dependency_overrides[get_scout_settings] = lambda: ctx.settings
    # Router unit test: provide a verified employee explicitly, never revive the removed shared-token auth.
    app.dependency_overrides[chat_api.require_write_token] = lambda: "vincenzo"
    response = TestClient(app).post("/v1/chat", json={"messages": [{"role": "user", "content": "How do I connect my mailbox in the dashboard?"}]})
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["step", "delta", "done"]
    assert "dashboard help" in events[0]["text"]
    assert "[Connections](/#connections)" in events[1]["text"]
    assert ctx.recorder.get(events[2]["task_id"]).status == "done"
    assert len(requests) == 2
    assert requests[0] == ["system", "user"]
    assert requests[1] == ["system", "user", "assistant", "tool"]
