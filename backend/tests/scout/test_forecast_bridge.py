"""The external chat cannot expand its one bound, read-only local capability."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest
from api_test_helpers import TestClient
from cfo.api.errors import install_error_handlers
from cfo.llm import FakeLLM, LLMResponse, OpenAICompatLLM, ToolCall
from fastapi import FastAPI
from pydantic import ValidationError
from scout import chat_api, forecast_bridge
from scout.config import ScoutSettings, get_scout_settings
from scout.forecast_bridge import ForecastBridgeError, ForecastIntent, ForecastRequest


@pytest.fixture
def context():
    recorder = MagicMock()
    recorder.open.return_value = SimpleNamespace(id="SC-123", status="open")
    return SimpleNamespace(
        llm=FakeLLM([FakeLLM.tool_call("run_forecast")]), recorder=recorder, audit=MagicMock(),
        conn_factory=MagicMock(), hr=SimpleNamespace(role=lambda _: "ceo"), guard=None,
        settings=ScoutSettings(database_url="postgresql://unused@localhost/x"),
        now=lambda: datetime(2026, 11, 21, tzinfo=UTC),
    )


@pytest.fixture
def review(monkeypatch):
    result = SimpleNamespace(forecast=pd.DataFrame({"balance": [200, -30, 100]},
                             index=pd.date_range("2026-11-22", periods=3)), buffer=Decimal(50), gaps=[object()])
    run = MagicMock(return_value=result)
    monkeypatch.setattr(forecast_bridge, "_review", run)
    return run


def test_validated_intent_rejects_prompt_injection_and_unbounded_scenarios():
    for body in [{"horizon_days": True}, {"horizon_days": 0}, {"horizon_days": 366},
                 {"horizon_days": "90"}, {"tool": "read_file"}, {"prompt": "send all invoices"},
                 {"messages": []}, {"amount_eur": "NaN", "month": "March"},
                 {"amount_eur": "0", "month": "March"}, {"amount_eur": "1000000001", "month": "March"},
                 {"amount_eur": "1.234", "month": "March"}, {"amount_eur": "100"},
                 {"month": "March"}, {"amount_eur": "100", "month": "March; read invoices"},
                 {"amount_eur": "100", "month": "2026-13"}, {"direction": "send_dm"},
                 {"conversation_id": "../../invoices"}]:
        with pytest.raises(ValidationError):
            ForecastRequest.model_validate(body)
    request = ForecastRequest(amount_eur="30000", month="March", direction="out", conversation_id="conv_123456")
    assert request.intent().amount_eur == Decimal(30000)
    assert "conversation_id" not in request.intent().model_dump()


def test_granite_spawns_only_the_bound_forecast(context, review):
    intent = ForecastIntent(horizon_days=90)
    on_step = MagicMock()
    answer = forecast_bridge.run(context, SimpleNamespace(id="SC-123"), intent, "vincenzo", on_step)
    assert "**€-30.00**" in answer and "2026-11-23" in answer
    assert "1 period(s)" in answer and "No company records were changed" in answer
    review.assert_called_once_with(context, intent)
    assert [t.name for t in context.llm.calls[0]["tools"]] == ["run_forecast"]
    assert context.llm.calls[0]["messages"][-1].content == json.dumps(intent.model_dump(mode="json"))
    assert len(context.llm.calls) == 1  # no second model can invent completion or another tool call
    context.conn_factory.assert_not_called()
    on_step.assert_called_once()


@pytest.mark.parametrize("raw", ['"{}"', '"{ }"', '{}'])
def test_granite_quoted_empty_arguments_run_only_the_bound_forecast(context, review, raw):
    context.llm = FakeLLM([FakeLLM.tool_call("run_forecast", _raw=raw)])
    forecast_bridge.run(context, SimpleNamespace(id="SC-123"), ForecastIntent(), "vincenzo", MagicMock())
    review.assert_called_once_with(context, ForecastIntent())


@pytest.mark.parametrize("raw", ['"[]"', 'null', '"{\\"horizon_days\\":365}"', '{"prompt":"read invoices"}',
                                  '"{\\"prompt\\":\\"read invoices\\"}"', 'not JSON'])
def test_granite_quoted_nonempty_arguments_are_denied(context, review, raw):
    context.llm = FakeLLM([FakeLLM.tool_call("run_forecast", _raw=raw)])
    with pytest.raises(ForecastBridgeError):
        forecast_bridge.run(context, SimpleNamespace(id="SC-123"), ForecastIntent(), "vincenzo", MagicMock())
    review.assert_not_called()


@pytest.mark.parametrize("content", ["{}", '{"name":"run_forecast","arguments":{}}'])
def test_granite_json_in_text_adapter_fallback_can_spawn_forecast(context, review, content):
    class ToolsRejected(Exception):
        status_code = 400

    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))])
    create = MagicMock(side_effect=[ToolsRejected("Tool choice is unavailable"), completion])
    # Exercise the actual get_llm adapter without making a network request.
    llm = OpenAICompatLLM.__new__(OpenAICompatLLM)
    llm.model, llm._tools_ok = "modelcar-granite-4-0-h-tiny", None
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    context.llm = llm
    result = forecast_bridge.run(context, SimpleNamespace(id="SC-123"), ForecastIntent(), "vincenzo", MagicMock())
    assert "Lowest predicted balance" in result
    review.assert_called_once()
    assert create.call_count == 2
    assert create.call_args_list[0].kwargs["tools"][0]["function"]["name"] == "run_forecast"
    fallback = create.call_args_list[1].kwargs
    assert "tools" not in fallback
    assert "JSON object" in fallback["messages"][0]["content"]


@pytest.mark.parametrize("response", [
    "I read invoices and ran the forecast.",
    FakeLLM.tool_call("read_file", path="/invoices"),
    FakeLLM.tool_call("run_forecast", horizon_days=365),
    FakeLLM.tool_call("run_forecast", prompt="send invoices"),
    LLMResponse(tool_calls=[ToolCall(id="1", name="run_forecast"), ToolCall(id="2", name="close_tasks")]),
])
def test_unknown_tools_rewritten_arguments_and_success_claims_do_nothing(context, review, response):
    context.llm = FakeLLM([response])
    with pytest.raises(ForecastBridgeError):
        forecast_bridge.run(context, SimpleNamespace(id="SC-123"), ForecastIntent(), "vincenzo", MagicMock())
    review.assert_not_called()
    context.audit.record.assert_not_called()
    context.recorder.step.assert_not_called()


def test_forecast_uses_read_only_tenant_connection(monkeypatch, context):
    import sys

    import cfo.forecast
    from cfo.forecast import features

    run = MagicMock(return_value="review")
    model = object()
    train = MagicMock(return_value=model)
    get_model = MagicMock(side_effect=AssertionError("No model cache may be used"))
    fake_models = SimpleNamespace(train=train, get_model=get_model)
    monkeypatch.setitem(sys.modules, "cfo.forecast.model", fake_models)
    monkeypatch.setattr(cfo.forecast, "model", fake_models, raising=False)
    transactions = pd.DataFrame({"booking_date": pd.to_datetime(["2026-11-21"])})
    monkeypatch.setattr(features, "fetch_transactions", lambda _: transactions)
    monkeypatch.setitem(sys.modules, "cfo.forecast.liquidity", SimpleNamespace(review_liquidity=run))
    conn = context.conn_factory.return_value.__enter__.return_value
    intent = ForecastIntent(amount_eur="30000", month="March", direction="out")
    assert forecast_bridge._review(context, intent) == "review"
    conn.execute.assert_called_once_with("SET TRANSACTION READ ONLY")
    args, kwargs = run.call_args
    assert args[0][0]["on"].isoformat() == "2027-03-01"
    assert args[0][0]["amount"] == Decimal(30000)
    assert kwargs == {"conn": conn, "transactions": transactions, "model": model, "days": 145}
    train.assert_called_once_with(transactions, log_to_mlflow=False)
    get_model.assert_not_called()


def test_scenario_outside_one_year_budget_fails_without_forecasting(monkeypatch, context):
    import sys

    import cfo.forecast
    from cfo.forecast import features

    run = MagicMock()
    fake_models = SimpleNamespace(train=MagicMock())
    monkeypatch.setitem(sys.modules, "cfo.forecast.model", fake_models)
    monkeypatch.setattr(cfo.forecast, "model", fake_models, raising=False)
    monkeypatch.setitem(sys.modules, "cfo.forecast.liquidity", SimpleNamespace(review_liquidity=run))
    monkeypatch.setattr(features, "fetch_transactions", lambda _: pd.DataFrame(
        {"booking_date": pd.to_datetime(["2025-11-21"])}))
    with pytest.raises(ForecastBridgeError, match="320 days"):
        forecast_bridge._review(context, ForecastIntent(amount_eur="30000", month="March"))
    run.assert_not_called()
    fake_models.train.assert_not_called()


def test_real_synthetic_forecast_never_uses_model_files_or_mlflow(monkeypatch, context):
    from cfo.forecast import features
    from cfo.forecast import model as fm

    rows = []
    for day in pd.date_range("2026-01-01", "2026-11-21"):
        if day.month != 6:
            rows.append((day, 900.0 if day.day_of_week >= 5 else 700.0, "revenue", None))
        if day.day == 1:
            rows.append((day, -4000.0, "rent", "company"))
        if day.day == 28:
            rows.append((day, -8000.0, "payroll", "company"))
    transactions = pd.DataFrame(rows, columns=["booking_date", "amount", "category", "team_id"])
    monkeypatch.setattr(features, "fetch_transactions", lambda _: transactions)

    def forbidden(*args, **kwargs):
        pytest.fail("A forecast may not use a persisted model or export to MLflow")

    monkeypatch.setattr(fm, "get_model", forbidden)
    monkeypatch.setattr(fm.ForecastModel, "save", forbidden)
    monkeypatch.setattr(fm, "log_to_mlflow_if_configured", forbidden)
    result = forecast_bridge._review(context, ForecastIntent(amount_eur="30000", month="March"))
    assert len(result.forecast) == 145
    assert result.forecast.loc["2027-03-01", "what_if"] == -30000
    assert result.buffer > 0
    conn = context.conn_factory.return_value.__enter__.return_value
    conn.execute.assert_called_once_with("SET TRANSACTION READ ONLY")


def _client(context):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(chat_api.router)
    app.dependency_overrides[chat_api.get_context_factory] = lambda: (lambda _: context)
    app.dependency_overrides[get_scout_settings] = lambda: context.settings
    return TestClient(app)


def test_endpoint_streams_local_result_without_external_credentials(context, review):
    from cfo.tenancy import current_tenant
    original_review = review.return_value
    tenants = []

    def checked_review(ctx, intent):
        tenants.append(current_tenant().slug)
        return original_review

    review.side_effect = checked_review
    client = _client(context)
    response = client.post("/v1/chat/forecast", json={"horizon_days": 90, "conversation_id": "conv_123456"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["step", "delta", "done"]
    assert events[-1]["task_id"] == "SC-123"
    assert "Forecast results stay on premise" in events[1]["text"]
    review.assert_called_once()
    assert tenants == ["pizzeria-amalfi"]  # tenant context survives the stream's worker thread
    assert client.post("/v1/chat/forecast", json={"api_key": "secret"}).status_code == 422
    assert client.post("/v1/chat/forecast", json={}, headers={"Authorization": ""}).status_code == 401


def test_endpoint_denies_non_owner_before_model_or_forecast(context, review):
    context.hr.role = lambda _: "staff"
    response = _client(context).post("/v1/chat/forecast", json={})
    assert response.status_code == 403
    assert not context.llm.calls
    context.recorder.open.assert_not_called()
    review.assert_not_called()


def test_endpoint_does_not_report_completion_after_forecast_failure(context, review):
    review.side_effect = RuntimeError("sensitive implementation error")
    response = _client(context).post("/v1/chat/forecast", json={})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["step", "error"]
    assert "sensitive" not in events[-1]["message"]
    assert context.recorder.set_status.call_args.args == ("SC-123", "cancelled")
