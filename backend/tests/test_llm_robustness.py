"""T041: small-model robustness of the LLM client, invoice extraction and the policy engine (fixes from the Codex
review). The agent-graph tests went with the December replay agent; `scout.policy` and `scout.llm_tasks` carry the
same code now. None of these tests needs a database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from cfo.domain import PolicyLevel, Transaction
from cfo.llm import FakeLLM, Message, OpenAICompatLLM, ToolSpec
from scout.llm_tasks import extract_invoice, parse_amount
from scout.policy import PolicyAction, PolicyContext, classify, context_from_transactions, parse_rule


# -- 17: odd LLM shapes never abort a turn ------------------------------------------------------------
@pytest.mark.parametrize("lines", [5, "Mozzarella 250", {"a": 1}, [None, 3, "x"], True])
def test_non_list_invoice_lines_are_ignored(lines):
    llm = FakeLLM([FakeLLM.tool_call("submit_invoice", number="AS-1", total="10.00", lines=lines)])
    ex = extract_invoice(llm, "Invoice AS-1 TOTAL 10.00")
    assert ex is not None and ex.total == Decimal("10.00") and ex.lines == []



class Completions:
    """Stand-in for `client.chat.completions` of the OpenAI SDK."""

    def __init__(self, *, arguments="{}", reject_tools=None, content="ok"):
        self.calls: list[dict] = []
        self.arguments, self.reject_tools, self.content = arguments, reject_tools, content

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("tools") and self.reject_tools is not None:
            raise self.reject_tools
        call = SimpleNamespace(id="c1", function=SimpleNamespace(name="submit", arguments=self.arguments))
        wants_tool = bool(kwargs.get("tools"))
        msg = SimpleNamespace(content=None if wants_tool else self.content, tool_calls=[call] if wants_tool else None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


def llm_with(completions) -> OpenAICompatLLM:
    llm = OpenAICompatLLM("http://localhost:1/v1", "granite")
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return llm


SUBMIT = ToolSpec(name="submit", description="d", parameters={"type": "object", "properties": {"x": {"type": "string"}}})
HI = [Message(role="user", content="hi")]


@pytest.mark.parametrize("arguments", ["[]", "null", "3", '"text"', "{broken"])
def test_tool_arguments_that_are_not_an_object_do_not_raise(arguments):
    out = llm_with(Completions(arguments=arguments)).chat(HI, [SUBMIT])
    (call,) = out.tool_calls
    assert isinstance(call.arguments, dict) and "x" not in call.arguments
    assert extract_invoice(SimpleNamespace(chat=lambda *a, **k: out), "text") is None


def test_object_arguments_are_kept():
    out = llm_with(Completions(arguments='{"x": "1"}')).chat(HI, [SUBMIT])
    assert out.tool_calls[0].arguments == {"x": "1"}


# -- 7: the endpoint rejects `tools` -> JSON-in-text path ---------------------------------------------
class Rejected(Exception):
    status_code = 400

    def __init__(self, text='"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set'):
        super().__init__(text)


def test_endpoint_without_tool_support_falls_back_to_json_in_text():
    comp = Completions(reject_tools=Rejected(), content='Sure: {"x": "42"}')
    llm = llm_with(comp)
    out = llm.chat(HI, [SUBMIT])
    assert [c.get("tools") for c in comp.calls] == [comp.calls[0]["tools"], None]
    assert "submit" in comp.calls[1]["messages"][0]["content"] and "JSON" in comp.calls[1]["messages"][0]["content"]
    (call,) = out.tool_calls  # the answer is parsed into the same shape a real tool call has
    assert call.name == "submit" and call.arguments == {"x": "42"}
    llm.chat(HI, [SUBMIT])  # remembered: no second rejected request
    assert [bool(c.get("tools")) for c in comp.calls] == [True, False, False]


def test_fallback_text_without_json_is_returned_as_plain_content():
    out = llm_with(Completions(reject_tools=Rejected(), content="no idea")).chat(HI, [SUBMIT])
    assert out.tool_calls == [] and out.content == "no idea"


def test_rejected_tools_with_two_tools_use_the_named_one():
    other = ToolSpec(name="other", description="d")
    comp = Completions(reject_tools=Rejected(), content='{"name": "other", "arguments": {"y": 1}}')
    out = llm_with(comp).chat(HI, [SUBMIT, other])
    assert out.tool_calls[0].name == "other" and out.tool_calls[0].arguments == {"y": 1}


def test_tool_calling_that_works_is_not_touched():
    comp = Completions(arguments='{"x": "1"}')
    out = llm_with(comp).chat(HI, [SUBMIT])
    assert len(comp.calls) == 1 and comp.calls[0]["tools"] and out.tool_calls[0].arguments == {"x": "1"}


def test_other_bad_requests_and_server_errors_are_not_swallowed():
    class TooLong(Exception):
        status_code = 400

    for err in (TooLong("maximum context length is 4096 tokens"), ConnectionError("down")):
        with pytest.raises(type(err)):
            llm_with(Completions(reject_tools=err)).chat(HI, [SUBMIT])


def test_rule_parsing_works_through_the_fallback():
    comp = Completions(reject_tools=Rejected(), content='{"level": "APPROVAL", "category": "equipment"}')
    rule = parse_rule("I approve equipment myself", llm_with(comp))
    assert rule.level == PolicyLevel.APPROVAL and rule.category == "equipment"



# -- P3: policy -----------------------------------------------------------------------------------------
def tx(i, name, amount):
    return Transaction(id=f"t{i}", booking_date=date(2025, 1 + i % 3, 5), type="GIRO", reference="r",
                       counterparty=name, amount=Decimal(amount))


def test_supplier_history_is_grouped_by_normalised_name():
    txns = [tx(0, "Metro AG", "-100"), tx(1, "METRO  AG ", "-300"), tx(2, "metro ag", "-200"), tx(3, "Shop", "1000")]
    ctx = context_from_transactions(txns)
    (key,) = [k for k in ctx.typical_amounts if k.casefold().split() == ["metro", "ag"]]
    assert ctx.typical_amounts[key] == Decimal("200")  # one median over all spellings
    d = classify(PolicyAction(action_type="pay_invoice", amount=Decimal("210"), supplier="Metro  AG"), ctx)
    assert d.level == PolicyLevel.AUTO and "Routine" in d.reason


def test_one_percent_message_matches_the_comparison():
    ctx = PolicyContext(avg_monthly_inflows=Decimal("100000"))
    d = classify(PolicyAction(action_type="pay_invoice", amount=Decimal("1000"), supplier="New Co"), ctx)
    assert d.level == PolicyLevel.APPROVAL  # exactly 1% is not "below 1%"
    assert "above 1%" not in d.reason and "at least 1%" in d.reason


@pytest.mark.parametrize("name", [["submit"], {"a": 1}, 3, None])
@pytest.mark.parametrize("tools", [[SUBMIT], [SUBMIT, ToolSpec(name="other", description="d")]])
def test_fallback_json_with_a_non_string_name_is_plain_text(name, tools):
    import json

    content = json.dumps({"name": name, "arguments": {"x": "1"}})
    out = llm_with(Completions(reject_tools=Rejected(), content=content)).chat(HI, tools)
    assert out.tool_calls == [] and out.content == content


@pytest.mark.parametrize("total", [float("inf"), float("-inf"), float("nan"), "inf", "NaN", 1e309, "1e309"])
def test_non_finite_totals_are_no_extraction(total):
    llm = FakeLLM([FakeLLM.tool_call("submit_invoice", number="AS-1", total=total)])
    assert extract_invoice(llm, "Invoice AS-1") is None


def test_non_finite_line_amounts_are_skipped():
    llm = FakeLLM([FakeLLM.tool_call("submit_invoice", number="AS-1", total="10.00", lines=[
        {"description": "bad", "amount": float("inf")}, {"description": "ok", "amount": "10.00"}])])
    ex = extract_invoice(llm, "Invoice AS-1")
    assert ex is not None and [ln.description for ln in ex.lines] == ["ok"]
    assert parse_amount(Decimal("Infinity")) is None and parse_amount(Decimal("NaN")) is None
    assert parse_amount(float("inf")) is None and parse_amount(1e309) is None


def test_rejected_temperature_is_dropped_and_max_tokens_renamed():
    """OpenAI reasoning models (gpt-5, o-series) reject `temperature` and want `max_completion_tokens`."""
    from types import SimpleNamespace

    from cfo.llm import Message, OpenAICompatLLM

    class Rejected(Exception):
        status_code = 400

    sent: list[dict] = []

    def create(**kwargs):  # noqa: ANN003
        sent.append(dict(kwargs))
        if "temperature" in kwargs:
            raise Rejected("Unsupported parameter: 'temperature' is not supported with this model.")
        if "max_tokens" in kwargs:
            raise Rejected("Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead.")
        msg = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    llm = OpenAICompatLLM.__new__(OpenAICompatLLM)
    llm.model, llm._tools_ok, llm._unsupported = "gpt-5", None, set()
    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert llm.chat([Message(role="user", content="hi")], max_tokens=50).content == "ok"
    assert "temperature" not in sent[-1] and sent[-1]["max_completion_tokens"] == 50
    sent.clear()
    assert llm.chat([Message(role="user", content="hi")], max_tokens=50).content == "ok"
    assert len(sent) == 1  # remembered: no second round of rejections


def test_reasoning_content_from_a_reasoning_model_is_ignored_and_never_echoed():
    """xAI reasoning models add `reasoning_content` next to `tool_calls`; it is neither read nor sent back."""

    class Reasoning:
        def __init__(self):
            self.calls: list[dict] = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            call = SimpleNamespace(id="c1", function=SimpleNamespace(name="submit", arguments='{"x": 1}'))
            first = len(self.calls) == 1
            msg = SimpleNamespace(content=None if first else "done", tool_calls=[call] if first else None,
                                  reasoning_content="PRIVATE-CHAIN-OF-THOUGHT")
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)

    api = Reasoning()
    llm = llm_with(api)
    tools = [ToolSpec(name="submit", description="d")]
    first = llm.chat([Message(role="user", content="go")], tools)
    assert first.tool_calls[0].name == "submit" and "PRIVATE" not in first.model_dump_json()
    llm.chat([Message(role="user", content="go"), first.as_message(),
              Message(role="tool", tool_call_id="c1", content="{}")], tools)
    assert "PRIVATE-CHAIN-OF-THOUGHT" not in str(api.calls[1]["messages"])
    assert all(set(m) <= {"role", "content", "tool_calls", "tool_call_id"} for m in api.calls[1]["messages"])
