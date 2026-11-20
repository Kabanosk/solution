"""Contract tests for the T001 interfaces: config defaults, domain round-trip, FakeLLM tool call."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from cfo.config import Settings
from cfo.domain import (ApprovalRequest, CostMetric, CostMetricKind, Decision,
                        Employee, Event, EventKind, Invoice, InvoiceLine,
                        PolicyLevel, Rule, Team, Transaction, Vacation)
from cfo.llm import LLM, FakeLLM, Message, ToolSpec


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "DATABASE_URL",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "WORKSTATIONS_ROOT",
        "SIM_SPEED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_config_defaults():
    s = Settings(_env_file=None)
    assert s.llm_model == "llama-3.2-3b"
    assert s.database_url == "postgresql://cfo:cfo@localhost:5433/cfo"
    assert s.llm_base_url.startswith("http")
    assert s.sim_speed == 1.0
    assert s.slack_bot_token == "" and s.slack_app_token == ""
    assert isinstance(s.workstations_root, Path)


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "other-model")
    monkeypatch.setenv("SIM_SPEED", "3600")
    s = Settings(_env_file=None)
    assert s.llm_model == "other-model"
    assert s.sim_speed == 3600


def test_policy_level_values():
    assert [p.value for p in PolicyLevel] == ["AUTO", "NOTIFY", "APPROVAL"]


def _samples():
    txn = Transaction(
        booking_date=date(2025, 12, 3),
        type="GIRO CARD PAYMENT",
        reference="Invoice AS-69664",
        counterparty="Asimex",
        amount=Decimal("-1234.50"),
        invoice_number="AS-69664",
    )
    inv = Invoice(
        number="AS-69664",
        supplier="Asimex",
        issue_date=date(2025, 12, 1),
        total=Decimal("1234.50"),
        lines=[
            InvoiceLine(
                description="Flour 25kg",
                quantity=Decimal(10),
                unit_price=Decimal("123.45"),
                amount=Decimal("1234.50"),
            )
        ],
        transaction_id=txn.id,
    )
    emp = Employee(
        id="grazyna",
        name="Grazyna",
        role="office manager",
        manager_id="vincenzo",
        vacations=[Vacation(start=date(2025, 12, 20), end=date(2025, 12, 31))],
    )
    return [
        txn,
        inv,
        emp,
        Team(
            id="kitchen",
            name="Kitchen",
            owner_id="rosa",
            monthly_budget=Decimal("9000"),
        ),
        CostMetric(
            kind=CostMetricKind.CLOUD_PER_ORDER,
            month=date(2025, 11, 1),
            cost=Decimal("300"),
            metric_value=Decimal("150"),
        ),
        Event(kind=EventKind.BANK_TRANSACTION, payload={"transaction_id": txn.id}),
        Decision(
            action_type="attach_invoice",
            summary="Attached AS-69664",
            level=PolicyLevel.AUTO,
            reason="known supplier",
            executed_simulated=True,
        ),
        ApprovalRequest(
            decision_id="dec_1", approver_id="vincenzo", question="Open a credit line?"
        ),
        Rule(
            text="equipment: always ask me",
            level=PolicyLevel.APPROVAL,
            category="equipment",
        ),
    ]


@pytest.mark.parametrize("model", _samples(), ids=lambda m: type(m).__name__)
def test_domain_round_trip(model):
    dumped = model.model_dump(mode="json")
    assert type(model).model_validate(dumped) == model
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_decision_defaults_not_simulated_and_vacation_logic():
    dec = Decision(action_type="x", summary="s", level=PolicyLevel.NOTIFY, reason="r")
    assert dec.executed_simulated is False
    emp = _samples()[2]
    assert emp.on_vacation(date(2025, 12, 24)) and not emp.on_vacation(
        date(2025, 12, 10)
    )
    cm = _samples()[4]
    assert cm.cost_per_unit == Decimal(2)


def test_fake_llm_tool_call():
    tools = [
        ToolSpec(
            name="get_balance",
            description="Balance on a day",
            parameters={"type": "object", "properties": {"day": {"type": "string"}}},
        )
    ]
    llm = FakeLLM(
        [FakeLLM.tool_call("get_balance", day="2025-06-01"), "Balance is -20k."]
    )
    assert isinstance(llm, LLM)

    messages = [Message(role="user", content="What is the balance on 1 June?")]
    first = llm.chat(messages, tools)
    assert first.tool_calls[0].name == "get_balance"
    assert first.tool_calls[0].arguments == {"day": "2025-06-01"}

    messages += [
        first.as_message(),
        Message(role="tool", tool_call_id=first.tool_calls[0].id, content="-20000"),
    ]
    second = llm.chat(messages, tools)
    assert second.content == "Balance is -20k." and not second.tool_calls
    assert len(llm.calls) == 2 and llm.calls[0]["tools"] == tools

    with pytest.raises(AssertionError):
        llm.chat(messages)


def test_tls_verify_setting_maps_strings():
    from cfo.llm import tls_verify_setting

    assert tls_verify_setting("true") is True
    assert tls_verify_setting("FALSE") is False
    assert (
        tls_verify_setting("/etc/llm-ca/service-ca.crt") == "/etc/llm-ca/service-ca.crt"
    )
    assert tls_verify_setting(False) is False
