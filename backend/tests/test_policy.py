"""Policy engine (D-005): classify(), CEO rule overrides, parse_rule(), context_from_transactions()."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from cfo.agent.policy import (
    PolicyAction,
    PolicyContext,
    PolicyDecision,
    classify,
    context_from_transactions,
    parse_rule,
)
from cfo.domain import PolicyLevel, Rule, Transaction
from cfo.llm import FakeLLM

D = Decimal
INFLOWS = D("100000")  # 1% = 1000, 5% = 5000


def ctx(**kw) -> PolicyContext:
    kw.setdefault("avg_monthly_inflows", INFLOWS)
    kw.setdefault("typical_amounts", {"Metro AG": D("4000"), "Cloud GmbH": D("500")})
    return PolicyContext(**kw)


def pay(amount, supplier="Metro AG", category="food", action_type="pay_invoice") -> PolicyAction:
    return PolicyAction(action_type=action_type, amount=D(amount), supplier=supplier, category=category)


# --- routine -> AUTO regardless of amount


def test_routine_known_supplier_is_auto_even_above_five_percent():
    d = classify(pay("-4500"), ctx())  # 4.5% of inflows, within +-20% of 4000
    assert d.level == PolicyLevel.AUTO
    assert "routine" in d.reason.lower()


def test_routine_boundary_is_inclusive_and_regardless_of_amount():
    big = ctx(typical_amounts={"Metro AG": D("20000")})
    assert classify(pay("-24000"), big).level == PolicyLevel.AUTO  # +20% exactly, 24% of inflows
    assert classify(pay("-16000"), big).level == PolicyLevel.AUTO  # -20% exactly
    assert classify(pay("-24001"), big).level == PolicyLevel.APPROVAL  # just outside, > 5%


def test_sign_of_amount_does_not_matter():
    assert classify(pay("4000"), ctx()).level == PolicyLevel.AUTO


def test_supplier_matching_ignores_case_and_whitespace():
    assert classify(pay("-4000", supplier="  metro ag "), ctx()).level == PolicyLevel.AUTO


# --- small amounts -> AUTO


def test_below_one_percent_is_auto_even_when_unusual():
    d = classify(pay("-999.99", supplier="Metro AG"), ctx())  # far from typical 4000, but < 1%
    assert d.level == PolicyLevel.AUTO
    assert "1%" in d.reason


def test_below_one_percent_is_auto_even_for_new_supplier():
    assert classify(pay("-50", supplier="Fresh Newcomer"), ctx()).level == PolicyLevel.AUTO


# --- unusual -> NOTIFY, big -> APPROVAL


def test_unusual_up_to_five_percent_is_notify():
    d = classify(pay("-3000"), ctx())  # 3%, typical 4000 -> -25%
    assert d.level == PolicyLevel.NOTIFY
    assert "unusual" in d.reason.lower()


def test_exactly_one_percent_and_exactly_five_percent_are_notify():
    assert classify(pay("-1000", supplier="Cloud GmbH"), ctx()).level == PolicyLevel.NOTIFY  # typical 500
    assert classify(pay("-5000"), ctx()).level == PolicyLevel.NOTIFY  # typical 4000, +25%


def test_unusual_above_five_percent_needs_approval():
    d = classify(pay("-5000.01"), ctx())
    assert d.level == PolicyLevel.APPROVAL
    assert "5%" in d.reason


def test_cloud_invoice_plus_fifty_percent_is_notify():
    # demo step 4: cloud invoice +50% m/m, still a small share of inflows
    d = classify(pay("-750", supplier="Cloud GmbH", category="cloud"), ctx(avg_monthly_inflows=D("50000")))
    assert d.level == PolicyLevel.NOTIFY


def test_new_supplier_above_one_percent_needs_approval():
    d = classify(pay("-1500", supplier="Fresh Newcomer"), ctx())
    assert d.level == PolicyLevel.APPROVAL
    assert "new supplier" in d.reason.lower()


def test_unknown_inflows_is_conservative_unless_routine():
    c = ctx(avg_monthly_inflows=None)
    assert classify(pay("-4000"), c).level == PolicyLevel.AUTO  # routine needs no inflows
    assert classify(pay("-3000"), c).level == PolicyLevel.APPROVAL
    assert classify(pay("-3000"), ctx(avg_monthly_inflows=D("0"))).level == PolicyLevel.APPROVAL


# --- always APPROVAL


@pytest.mark.parametrize(
    "action_type",
    ["request_credit_line", "open_deposit", "make_investment", "fx_hedge", "change_team_budget"],
)
def test_strategic_actions_always_need_approval(action_type):
    d = classify(PolicyAction(action_type=action_type, amount=D("1")), ctx())
    assert d.level == PolicyLevel.APPROVAL


def test_strategic_action_without_amount_needs_approval():
    assert classify(PolicyAction(action_type="request_credit_line"), ctx()).level == PolicyLevel.APPROVAL


def test_action_without_money_is_auto():
    d = classify(PolicyAction(action_type="attach_invoice", supplier="Metro AG"), ctx())
    assert d.level == PolicyLevel.AUTO
    assert "no money" in d.reason.lower()


# --- CEO overrides


def rule(level, created_at=None, **match) -> Rule:
    r = Rule(text=f"{match} -> {level}", level=level, **match)
    if created_at:
        r.created_at = created_at
    return r


def test_ceo_rule_beats_routine():
    c = ctx(rules=[rule(PolicyLevel.APPROVAL, category="equipment")])
    d = classify(pay("-4000", category="equipment"), c)  # would be routine AUTO
    assert d.level == PolicyLevel.APPROVAL
    assert "ceo rule" in d.reason.lower()


def test_ceo_rule_can_relax_a_default():
    c = ctx(rules=[rule(PolicyLevel.AUTO, supplier="Fresh Newcomer")])
    assert classify(pay("-4000", supplier="Fresh Newcomer"), c).level == PolicyLevel.AUTO


def test_ceo_rule_can_relax_strategic_action():
    c = ctx(rules=[rule(PolicyLevel.NOTIFY, action_type="open_deposit")])
    assert classify(PolicyAction(action_type="open_deposit", amount=D("10")), c).level == PolicyLevel.NOTIFY


def test_rule_requires_every_given_field_to_match():
    c = ctx(rules=[rule(PolicyLevel.APPROVAL, category="equipment", supplier="Dell")])
    assert classify(pay("-4000", category="equipment"), c).level == PolicyLevel.AUTO
    assert classify(pay("-4000", supplier="Dell", category="equipment"), c).level == PolicyLevel.APPROVAL


def test_rule_category_match_is_case_insensitive_and_text_is_quoted_in_reason():
    r = Rule(text="equipment I always approve myself", level=PolicyLevel.APPROVAL, category="equipment")
    d = classify(pay("-10", category="Equipment"), ctx(rules=[r]))
    assert d.level == PolicyLevel.APPROVAL
    assert "equipment I always approve myself" in d.reason


def test_newest_matching_rule_wins():
    now = datetime.now(timezone.utc)
    old = rule(PolicyLevel.APPROVAL, created_at=now - timedelta(days=2), category="equipment")
    new = rule(PolicyLevel.NOTIFY, created_at=now, category="equipment")
    for order in ([old, new], [new, old]):
        assert classify(pay("-10", category="equipment"), ctx(rules=order)).level == PolicyLevel.NOTIFY


def test_inactive_and_empty_rules_are_ignored():
    inactive = rule(PolicyLevel.APPROVAL, category="equipment")
    inactive.active = False
    empty = Rule(text="everything", level=PolicyLevel.APPROVAL)  # no match field: never matches
    d = classify(pay("-4000", category="equipment"), ctx(rules=[inactive, empty]))
    assert d.level == PolicyLevel.AUTO


def test_decision_is_a_model_with_level_and_reason():
    d = classify(pay("-4000"), ctx())
    assert isinstance(d, PolicyDecision) and d.reason.endswith(".")
    assert PolicyDecision.model_validate(d.model_dump(mode="json")) == d


# --- context_from_transactions


def txn(day, amount, who, category=None) -> Transaction:
    return Transaction(booking_date=day, type="X", reference="r", counterparty=who, amount=D(amount), category=category)


def test_context_from_transactions_derives_typical_and_inflows():
    txns = [
        txn(date(2025, 1, 10), "30000", "Card settlement"),
        txn(date(2025, 2, 10), "50000", "Card settlement"),
        txn(date(2025, 1, 12), "-100", "Metro AG"),
        txn(date(2025, 1, 20), "-300", "Metro AG"),
        txn(date(2025, 2, 20), "-200", "Metro AG"),
        txn(date(2025, 2, 21), "-50", "Cloud GmbH"),
    ]
    c = context_from_transactions(txns, rules=[])
    assert c.avg_monthly_inflows == D("40000")  # 80000 over 2 months
    assert c.typical_amounts["Metro AG"] == D("200")  # median of absolute outflows
    assert "Card settlement" not in c.typical_amounts  # inflows are not suppliers
    assert classify(pay("-210", supplier="Metro AG"), c).level == PolicyLevel.AUTO


def test_context_from_no_transactions():
    c = context_from_transactions([], rules=[])
    assert c.avg_monthly_inflows is None and c.typical_amounts == {}


# --- parse_rule


def test_parse_rule_from_tool_call():
    llm = FakeLLM([FakeLLM.tool_call("create_rule", level="APPROVAL", category="equipment")])
    r = parse_rule("sprzęt zawsze zatwierdzam sam", llm)
    assert isinstance(r, Rule)
    assert (r.level, r.category, r.supplier, r.action_type) == (PolicyLevel.APPROVAL, "equipment", None, None)
    assert r.text == "sprzęt zawsze zatwierdzam sam" and r.created_by == "ceo" and r.active
    sent = llm.calls[0]
    assert sent["tools"] and sent["tools"][0].name == "create_rule"
    assert "sprzęt zawsze zatwierdzam sam" in sent["messages"][-1].content


def test_parse_rule_normalises_values():
    llm = FakeLLM([FakeLLM.tool_call("create_rule", level="auto", supplier="  Metro AG ", category="Food ")])
    r = parse_rule("Metro AG food: just do it", llm)
    assert r.level == PolicyLevel.AUTO and r.supplier == "Metro AG" and r.category == "food"


def test_parse_rule_falls_back_to_json_content():
    llm = FakeLLM(['Sure: {"level": "NOTIFY", "action_type": "request_credit_line"}'])
    r = parse_rule("tell me about credit", llm)
    assert r.level == PolicyLevel.NOTIFY and r.action_type == "request_credit_line"


def test_parse_rule_mentions_known_categories_in_prompt():
    llm = FakeLLM([FakeLLM.tool_call("create_rule", level="APPROVAL", category="cloud")])
    parse_rule("cloud always ask", llm, categories=["food", "cloud"])
    prompt = " ".join(m.content or "" for m in llm.calls[0]["messages"])
    assert "food" in prompt and "cloud" in prompt


@pytest.mark.parametrize(
    "response",
    [
        FakeLLM.tool_call("create_rule", level="MAYBE", category="x"),  # bad level
        FakeLLM.tool_call("create_rule", level="AUTO"),  # nothing to match
        FakeLLM.tool_call("something_else", level="AUTO", category="x"),
        "I do not understand",
    ],
)
def test_parse_rule_rejects_unusable_answers(response):
    with pytest.raises(ValueError):
        parse_rule("blah", FakeLLM([response]))


def test_parse_rule_rejects_blank_text_without_calling_llm():
    llm = FakeLLM()
    with pytest.raises(ValueError):
        parse_rule("   ", llm)
    assert llm.calls == []
