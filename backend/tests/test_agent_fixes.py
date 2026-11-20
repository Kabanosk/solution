"""T041: agent graph correctness and small-model robustness (fixes from the Codex review).

Each test pins one finding of `reports/review-fixes.md`. Agent tests use the `make_env` fixture of test_agent
(isolated per-process Postgres schema, FakeLLM, fake notifier); the LLM-client and policy tests need no database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

import test_agent
from test_agent import DOC, FORECAST, TS, FakeNotifier, bank, decisions, fake_llm_handler, make_env, seed_cloud, txn  # noqa: F401
from cfo.agent.graph import AgentConfig, build_agent
from cfo.agent.llm_tasks import extract_invoice
from cfo.agent.policy import PolicyAction, PolicyContext, classify, context_from_transactions
from cfo.domain import (
    ApprovalStatus, Team, CostMetric, CostMetricKind, DecisionStatus, Employee, Event, EventKind, Invoice, InvoiceLine,
    PolicyLevel, Rule, Transaction, Vacation,
)
from cfo.llm import FakeLLM, LLMResponse, Message, OpenAICompatLLM, ToolCall, ToolSpec


# -- 12: the extracted invoice number decides, not the expected one ---------------------------------
def test_document_that_only_mentions_the_expected_number_is_not_attached(make_env):
    doc = "Invoice AS-11111\nSee also AS-69664 (credit note)\nTOTAL: 250.00\n"
    env = make_env(files={"Downloads/other.txt": doc})
    env.agent.handle(bank("txn_0"))
    assert not txn(env, "txn_0").documented
    assert decisions(env, "request_invoice") and not decisions(env, "attach_invoice")


def test_extracted_number_is_compared_loosely(make_env):
    env = make_env(files={"Downloads/Asimex-december.txt": "Invoice AS 69664\nTOTAL: 250.00"})
    env.agent.handle(bank("txn_0"))
    assert txn(env, "txn_0").documented  # "AS 69664" and "AS-69664" are the same number


# -- 13: an approved action that the tool refuses is not EXECUTED -----------------------------------
def test_approved_attach_that_is_refused_is_recorded_as_failed_and_the_ceo_is_told(make_env):
    env = make_env(files={"Downloads/AS-69664.txt": DOC})
    with env.factory() as r:
        r.add_rule(Rule(text="ask me before attaching", level=PolicyLevel.APPROVAL, action_type="attach_invoice"))
    res = env.agent.handle(bank("txn_0"))
    assert res.status == "awaiting_approval"
    # while the CEO thinks about it, somebody documents the payment: the tool will refuse the approved action
    env.tools.call("company-db", "attach_invoice", transaction_id="txn_0", number="AS-69664", supplier="Asimex",
                   issue_date="2025-12-03", total="250.00")
    sent = len(env.notifier.sent)
    env.agent.resume_approval(res.approval_id, True, "vincenzo")
    (dec,) = decisions(env, "attach_invoice")
    assert dec.status != DecisionStatus.EXECUTED
    assert "failed" in dec.summary.lower() and dec.payload.get("failed") is True
    msg = env.notifier.sent[sent:]
    assert msg and msg[-1]["to"] == "vincenzo" and "not" in msg[-1]["text"].lower()
    assert not msg[-1]["text"].startswith("Approved.")


def test_approved_attach_that_works_is_still_executed(make_env):
    env = make_env(files={"Downloads/AS-69664.txt": DOC})
    with env.factory() as r:
        r.add_rule(Rule(text="ask me before attaching", level=PolicyLevel.APPROVAL, action_type="attach_invoice"))
    res = env.agent.handle(bank("txn_0"))
    env.agent.resume_approval(res.approval_id, True, "vincenzo")
    assert txn(env, "txn_0").documented
    (dec,) = decisions(env, "attach_invoice")
    assert dec.status == DecisionStatus.EXECUTED


# -- 14: a CEO rule can put anomaly alerts at APPROVAL ----------------------------------------------
def metric_event():
    return Event(ts=TS, kind=EventKind.METRIC, payload={})


def alert_rule(env):
    with env.factory() as r:
        r.add_rule(Rule(text="cost alerts: ask me first", level=PolicyLevel.APPROVAL, action_type="alert_cost_anomaly"))


def test_alert_rule_at_approval_interrupts_instead_of_sending(make_env):
    env = make_env()
    seed_cloud(env)
    alert_rule(env)
    res = env.agent.handle(metric_event())
    assert res.status == "awaiting_approval" and res.approval_id
    (dm,) = env.notifier.sent  # only the approval question to the CEO, no alert yet
    assert dm["to"] == "vincenzo" and dm["level"] == PolicyLevel.APPROVAL and dm["actions"]
    (dec,) = decisions(env, "alert_cost_anomaly")
    assert dec.level == PolicyLevel.APPROVAL and dec.status == DecisionStatus.PENDING

    env.agent.resume_approval(res.approval_id, True, "vincenzo")
    (dec,) = decisions(env, "alert_cost_anomaly")
    assert dec.status == DecisionStatus.EXECUTED and not dec.executed_simulated
    alert = env.notifier.sent[1]
    assert alert["to"] == "vincenzo" and alert["level"] == PolicyLevel.NOTIFY and not alert["actions"]
    assert alert["text"] == dec.summary.split(" Approved by")[0]


def test_rejected_alert_is_not_sent_and_not_repeated(make_env):
    env = make_env()
    seed_cloud(env)
    alert_rule(env)
    res = env.agent.handle(metric_event())
    env.agent.resume_approval(res.approval_id, False, "vincenzo")
    sent = len(env.notifier.sent)
    (dec,) = decisions(env, "alert_cost_anomaly")
    assert dec.status == DecisionStatus.REJECTED
    assert env.agent.handle(metric_event()).status == "done"  # same month: already decided, not asked again
    assert len(env.notifier.sent) == sent and len(decisions(env, "alert_cost_anomaly")) == 1


def test_pending_alert_is_not_asked_twice(make_env):
    env = make_env()
    seed_cloud(env)
    alert_rule(env)
    env.agent.handle(metric_event())
    env.agent.handle(metric_event())
    assert len(decisions(env, "alert_cost_anomaly")) == 1


# -- 15: the credit line headline names the date the line is needed from -----------------------------
def test_credit_line_headline_uses_the_proposal_start_date(make_env):
    env = make_env()
    ev = Event(ts=TS, kind=EventKind.METRIC, payload={**FORECAST.payload, "period_start": "2026-03-01",
                                                      "period_end": "2026-07-15"})
    res = env.agent.handle(ev)
    (dec,) = decisions(env, "request_credit_line")
    assert "from 1 March 2026" in dec.summary and "June" not in dec.summary
    assert "from 1 March 2026" in env.notifier.to("vincenzo")[0]["text"]
    assert res.status == "awaiting_approval"


def test_credit_line_headline_falls_back_to_the_month_without_a_start_date(make_env):
    env = make_env()
    env.agent.handle(FORECAST)
    (dec,) = decisions(env, "request_credit_line")
    assert "2026-06" in dec.summary


# -- 16: ad-spend alerts list the contributing invoice lines -----------------------------------------
def test_ads_alert_names_invoice_lines_from_the_repository(make_env):
    env = make_env()
    ads = CostMetricKind.ADS_CAC
    with env.factory() as r:
        r.add_team(Team(id="marketing", name="Marketing", owner_id="vincenzo"))
        r.add_cost_metric(CostMetric(kind=ads, month=date(2025, 11, 1), team_id="marketing", cost=Decimal("800"),
                                     metric_value=Decimal("60"), detail={"invoice_number": "ADS-11"}))
        r.add_cost_metric(CostMetric(kind=ads, month=date(2025, 12, 1), team_id="marketing", cost=Decimal("1200"),
                                     metric_value=Decimal("60"), detail={"invoice_number": "ADS-12"}))
        for number, social, search in [("ADS-11", "500", "300"), ("ADS-12", "500", "700")]:
            r.add_invoice(Invoice(number=number, supplier="Metaads", issue_date=date(2025, 12, 31),
                                  total=Decimal(social) + Decimal(search),
                                  lines=[InvoiceLine(description="Social", unit_price=Decimal(social), amount=Decimal(social)),
                                         InvoiceLine(description="Search", unit_price=Decimal(search), amount=Decimal(search))]))
    env.agent.handle(metric_event())
    (dec,) = decisions(env, "alert_cost_anomaly")
    assert dec.payload["facts"]["biggest_increase"]["line"] == "Search"


# -- 17: odd LLM shapes never abort a turn ------------------------------------------------------------
@pytest.mark.parametrize("lines", [5, "Mozzarella 250", {"a": 1}, [None, 3, "x"], True])
def test_non_list_invoice_lines_are_ignored(lines):
    llm = FakeLLM([FakeLLM.tool_call("submit_invoice", number="AS-1", total="10.00", lines=lines)])
    ex = extract_invoice(llm, "Invoice AS-1 TOTAL 10.00")
    assert ex is not None and ex.total == Decimal("10.00") and ex.lines == []


def test_scalar_lines_do_not_abort_reconciliation(make_env):
    def handler(messages, tools):
        if any(t.name == "submit_invoice" for t in tools or []):
            return FakeLLM.tool_call("submit_invoice", number="AS-69664", total="250.00", lines=7)
        return fake_llm_handler(messages, tools)

    env = make_env(llm=FakeLLM(handler=handler), files={"Downloads/AS-69664.txt": DOC})
    env.agent.handle(bank("txn_0"))
    assert txn(env, "txn_0").documented


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
    from cfo.agent.policy import parse_rule

    comp = Completions(reject_tools=Rejected(), content='{"level": "APPROVAL", "category": "equipment"}')
    rule = parse_rule("I approve equipment myself", llm_with(comp))
    assert rule.level == PolicyLevel.APPROVAL and rule.category == "equipment"


# -- 24: a notifier failure does not strand an approval -----------------------------------------------
class FlakyNotifier(FakeNotifier):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def send_dm(self, employee_id, text, level, actions=()):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("slack is down")
        return super().send_dm(employee_id, text, level, actions)


def test_approval_request_that_could_not_be_delivered_is_re_sent_on_the_next_event(make_env):
    env = make_env()
    flaky = FlakyNotifier(failures=1)
    agent = build_agent(env.tools, env.factory, env.llm, flaky, AgentConfig())
    res = agent.handle(FORECAST)
    assert res.status == "awaiting_approval" and flaky.sent == []
    with env.factory() as r:
        assert r.get_approval_request(res.approval_id).channel_ref is None  # visible as undelivered
    agent.handle(Event(ts=TS, kind=EventKind.MESSAGE, payload={"text": "hi"}))  # any later event retries
    (dm,) = flaky.sent
    assert dm["to"] == "vincenzo" and [a.value for a in dm["actions"]] == [res.approval_id, res.approval_id]
    with env.factory() as r:
        assert r.get_approval_request(res.approval_id).channel_ref == "ref-1"
    agent.handle(Event(ts=TS, kind=EventKind.MESSAGE, payload={"text": "again"}))
    assert len(flaky.sent) == 1  # delivered once, not re-sent
    assert agent.resume_approval(res.approval_id, True, "vincenzo").status == "done"


def test_redelivery_that_fails_again_stays_undelivered_and_never_breaks_the_event(make_env):
    env = make_env()
    flaky = FlakyNotifier(failures=2)
    agent = build_agent(env.tools, env.factory, env.llm, flaky, AgentConfig())
    res = agent.handle(FORECAST)
    assert agent.handle(Event(ts=TS, kind=EventKind.MESSAGE, payload={})).status == "ignored"
    assert flaky.sent == []
    agent.handle(Event(ts=TS, kind=EventKind.MESSAGE, payload={}))
    assert len(flaky.sent) == 1
    with env.factory() as r:
        assert r.get_approval_request(res.approval_id).status == ApprovalStatus.PENDING


def test_resolved_approvals_are_not_re_sent(make_env):
    env = make_env()
    flaky = FlakyNotifier(failures=1)
    agent = build_agent(env.tools, env.factory, env.llm, flaky, AgentConfig())
    res = agent.handle(FORECAST)
    agent.resume_approval(res.approval_id, False, "vincenzo")
    sent = len(flaky.sent)
    agent.handle(Event(ts=TS, kind=EventKind.MESSAGE, payload={}))
    assert len(flaky.sent) == sent


# -- P3: escalation history and vacations -------------------------------------------------------------
def test_escalation_history_is_looked_up_per_transaction_not_in_the_last_500_decisions(make_env):
    env = make_env()
    env.agent.handle(bank("txn_1"))
    from cfo.domain import Decision

    with env.factory() as r:
        for i in range(520):  # newer decisions of other payments push the request out of any global window
            r.add_decision(Decision(action_type="alert_cost_anomaly", level=PolicyLevel.NOTIFY, reason="x",
                                    summary=f"noise {i}", payload={"kind": "k", "month": f"m{i}"}))
    env.agent.handle(bank("txn_1", EventKind.REMINDER))
    with env.factory() as r:
        mine = r._all("SELECT action_type FROM decisions WHERE payload->>'transaction_id' = 'txn_1'")
    types = [m["action_type"] for m in mine]
    assert sorted(types) == ["remind_invoice", "request_invoice"]  # a reminder, not a second first request


def test_reminder_skips_a_recipient_who_went_on_vacation_meanwhile(make_env):
    env = make_env()
    env.agent.handle(bank("txn_1"))  # asks grazyna
    with env.factory() as r:
        r.add_employee(Employee(id="grazyna", name="Grazyna", role="office manager", manager_id="rosa",
                                vacations=[Vacation(start=date(2025, 12, 1), end=date(2025, 12, 31))]))
    before = len(env.notifier.to("grazyna"))
    env.agent.handle(bank("txn_1", EventKind.REMINDER))
    latest = decisions(env)[0]
    assert latest.action_type == "escalate_invoice" and latest.payload["recipient"] == "rosa"
    assert "vacation" in env.notifier.to("rosa")[0]["text"]
    assert len(env.notifier.to("grazyna")) == before  # no reminder to someone who is away


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


# -- review round: approved alert nobody received, odd fallback name, non-finite amounts -----------------
class DownNotifier(FlakyNotifier):
    """Delivers the first `ok_first` DMs, then fails for good."""

    def __init__(self, ok_first):
        super().__init__(failures=0)
        self.ok_first = ok_first

    def send_dm(self, employee_id, text, level, actions=()):
        if len(self.sent) >= self.ok_first:
            raise RuntimeError("slack is down")
        return super().send_dm(employee_id, text, level, actions)


def test_approved_alert_that_reached_nobody_is_failed_and_retried_later(make_env):
    env = make_env()
    seed_cloud(env)
    alert_rule(env)
    notifier = DownNotifier(ok_first=1)  # the approval question gets through, the alert does not
    agent = build_agent(env.tools, env.factory, env.llm, notifier, AgentConfig())
    res = agent.handle(metric_event())
    agent.resume_approval(res.approval_id, True, "vincenzo")
    (dec,) = decisions(env, "alert_cost_anomaly")
    assert dec.status != DecisionStatus.EXECUTED and dec.payload.get("failed") is True
    assert "not" in dec.summary.lower()
    # a later run is not suppressed by the failed month: the CEO is asked again
    again = agent.handle(metric_event())
    assert again.status == "awaiting_approval"


def test_approved_alert_that_reached_someone_is_executed(make_env):
    env = make_env()
    seed_cloud(env)
    alert_rule(env)
    res = env.agent.handle(metric_event())
    env.agent.resume_approval(res.approval_id, True, "vincenzo")
    (dec,) = decisions(env, "alert_cost_anomaly")
    assert dec.status == DecisionStatus.EXECUTED and not dec.payload.get("failed")


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
    from cfo.agent.llm_tasks import parse_amount

    llm = FakeLLM([FakeLLM.tool_call("submit_invoice", number="AS-1", total="10.00", lines=[
        {"description": "bad", "amount": float("inf")}, {"description": "ok", "amount": "10.00"}])])
    ex = extract_invoice(llm, "Invoice AS-1")
    assert ex is not None and [ln.description for ln in ex.lines] == ["ok"]
    assert parse_amount(Decimal("Infinity")) is None and parse_amount(Decimal("NaN")) is None
    assert parse_amount(float("inf")) is None and parse_amount(1e309) is None
