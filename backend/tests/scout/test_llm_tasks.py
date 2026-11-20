from datetime import date
from decimal import Decimal

from cfo.llm import FakeLLM
from scout.llm_tasks import classify_chat, classify_reply, route_dm

FACTS = {"supplier": "Pasta Fresca Bologna S.r.l.", "amount_eur": "1250.00", "invoice": "PFB-2611"}


def test_classify_chat_refund():
    llm = FakeLLM([FakeLLM.tool_call("classify_message", label="refund_expected", refund_amount="1,250.00",
                                     refund_by="2026-11-28", reason="they will refund")])
    v = classify_chat(llm, "They will refund the 1,250.00 deposit by the 28th", FACTS)
    assert v.label == "refund_expected" and v.refund_amount == Decimal("1250.00") and v.refund_by == date(2026, 11, 28)


def test_classify_garbage_is_unrelated():
    assert classify_chat(FakeLLM(["sure!"]), "x", FACTS).label == "unrelated"
    assert classify_chat(FakeLLM([FakeLLM.tool_call("classify_message", label="banana")]), "x", FACTS).label == "unrelated"


def test_classify_model_down_is_unrelated():
    def boom(*_):
        raise RuntimeError("down")

    assert classify_chat(FakeLLM(handler=boom), "x", FACTS).label == "unrelated"


def test_classify_reply():
    llm = FakeLLM([FakeLLM.tool_call("classify_reply", label="explains", category="Team events")])
    assert classify_reply(llm, "offsite lunch", FACTS) == ("explains", "Team events")


def test_route_dm_what_if():
    llm = FakeLLM([FakeLLM.tool_call("route_message", intent="what_if", amount_eur="30000", month="March", what="oven")])
    out = route_dm(llm, "what if I buy an oven for 30k in March?")
    assert out["intent"] == "what_if" and out["amount_eur"] == "30000"


def test_route_dm_fallback_smalltalk():
    assert route_dm(FakeLLM(["hello"]), "hi")["intent"] == "smalltalk"
