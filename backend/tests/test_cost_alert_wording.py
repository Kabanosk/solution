"""T049: the cost-vs-metric alert always states the cost change (percent, EUR before -> now), the metric change
and the cost per unit in EUR. The LLM may word it; code checks the wording and falls back to the template.

No database: the investigation graph only needs `cost_metrics()` and `invoice()` from its gateway.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from cfo.agent.investigate import build_investigation_graph
from cfo.analysis.cost_metrics import detect_anomalies
from cfo.domain import CostMetric, CostMetricKind
from cfo.llm import FakeLLM

CLOUD = CostMetricKind.CLOUD_PER_ORDER


def metrics(cost_before="200", cost_now="300", orders_before="1000", orders_now="1030"):
    return [
        CostMetric(kind=CLOUD, month=date(2025, 11, 1), team_id="it", cost=Decimal(cost_before),
                   metric_value=Decimal(orders_before),
                   detail={"lines": [{"service": "Compute", "amount": cost_before}]}),
        CostMetric(kind=CLOUD, month=date(2025, 12, 1), team_id="it", cost=Decimal(cost_now),
                   metric_value=Decimal(orders_now),
                   detail={"lines": [{"service": "Compute", "amount": cost_now}]}),
    ]


class StubGateway:
    def __init__(self, rows):
        self.rows = rows

    def cost_metrics(self, kind=None):
        return [m.model_dump(mode="json") for m in self.rows]

    def invoice(self, number):
        return None


def investigate(reply, **kw):
    llm = FakeLLM(handler=lambda messages, tools: reply)
    out = build_investigation_graph(StubGateway(metrics(**kw)), llm).invoke({})
    (finding,) = out["findings"]
    return finding, llm


# -- deterministic sentence (cfo.analysis.cost_metrics) ---------------------------------------------------
def test_template_states_cost_change_in_percent_and_eur_and_the_metric_change():
    (a,) = detect_anomalies(metrics())
    assert "rose 50.0%" in a.explanation
    assert "from EUR 200.00 to EUR 300.00" in a.explanation
    assert "online orders grew only 3.0%" in a.explanation
    assert "cloud cost per online order from EUR 0.20 to EUR 0.29" in a.explanation
    assert a.explanation.count(". ") == 0 and a.explanation.endswith(".")  # still one sentence


def test_template_has_eur_even_without_line_items_and_per_unit_cost():
    rows = metrics(orders_before="0", orders_now="0")
    for m in rows:
        m.detail.clear()
    (a,) = detect_anomalies(rows)
    assert "rose 50.0%" in a.explanation and "from EUR 200.00 to EUR 300.00" in a.explanation
    assert "did not change" in a.explanation


# -- facts and instruction given to the LLM ---------------------------------------------------------------
def test_facts_carry_eur_amounts_and_rounded_percents():
    finding, llm = investigate("x" * 20, cost_now="291.26")
    facts = finding["facts"]
    assert facts["currency"] == "EUR" and "new_cost" not in facts
    assert facts["what"] == "Cloud cost for the ordering app"  # the cost, not the cost per order, leads
    assert facts["cost_before_eur"] == "200.00" and facts["cost_now_eur"] == "291.26"
    assert facts["cost_change_pct"] == 45.6 and facts["metric_change_pct"] == 3.0
    assert facts["cost_per_unit_change_pct"] == 41.4
    assert facts["cost_per_unit_now"] == "0.28" and facts["cost_per_unit_before"] == "0.20"
    json.dumps(facts)  # stored in the decision payload
    user = llm.calls[0]["messages"][-1].content
    lead, metric, unit = user.index("cost change"), user.index("metric change"), user.index("cost per unit")
    assert lead < metric < unit and "EUR" in user[:user.index("Facts:")]


# -- guard --------------------------------------------------------------------------------------------------
def test_llm_sentence_without_the_cost_change_is_replaced_by_the_template():
    reply = "The cloud cost per online order rose by 45.63% to 0.29 units while online orders grew 3%."
    finding, _ = investigate(reply)
    assert finding["explanation"] == finding["summary"] != reply
    assert "50" in finding["explanation"] and "EUR" in finding["explanation"]


def test_llm_sentence_with_the_cost_change_is_kept():
    for reply in (
        "Cloud cost rose 50% from EUR 200.00 to EUR 300.00 while online orders grew 3%; that is EUR 0.29 per order.",
        "Cloud cost rose 50.0 % (200 € to 300 €) while online orders grew only 3.0 %.",
    ):
        finding, _ = investigate(reply)
        assert finding["explanation"] == reply


def test_other_numbers_do_not_count_as_the_cost_change():
    for reply in (
        "Cloud cost went up to EUR 300.50 while online orders grew 3%, which is EUR 0.29 per order.",  # 50 in 300.50
        "Cloud cost rose 150% from EUR 200.00 to EUR 300.00 while online orders grew 3%.",
        "Cloud cost rose 50.4% from EUR 200.00 to EUR 300.00 while online orders grew 3%.",
    ):
        finding, _ = investigate(reply)
        assert finding["explanation"] == finding["summary"], reply


def test_llm_sentence_without_eur_is_replaced():
    finding, _ = investigate("Cloud cost rose 50% from 200 units to 300 units while online orders grew 3%.")
    assert finding["explanation"] == finding["summary"]


def test_one_decimal_percent_must_match():
    kept = "Cloud cost rose 45.6% from EUR 200.00 to EUR 291.26 while online orders grew 3%."
    finding, _ = investigate(kept, cost_now="291.26")
    assert finding["explanation"] == kept
    finding, _ = investigate(kept.replace("45.6%", "41.4%"), cost_now="291.26")
    assert finding["explanation"] == finding["summary"] and "45.6%" in finding["summary"]


# -- cost rising from zero (T044: cost_change_pct is None) ------------------------------------------------
def test_new_cost_has_no_percent_and_uses_new_cost_wording():
    reply = "The cloud cost per online order is now 0.15 units."
    finding, _ = investigate(reply, cost_before="0", cost_now="150", orders_now="1000")
    facts = finding["facts"]
    assert facts["new_cost"] is True and facts["cost_change_pct"] is None
    assert facts["cost_before_eur"] == "0.00" and facts["cost_now_eur"] == "150.00"
    assert finding["explanation"] == finding["summary"]
    assert "new cost" in finding["explanation"] and "EUR 150.00" in finding["explanation"]


def test_new_cost_sentence_from_the_llm_is_kept_when_it_says_new():
    reply = "Cloud is a new cost this month: EUR 150.00, up from EUR 0.00, while online orders did not change."
    finding, _ = investigate(reply, cost_before="0", cost_now="150", orders_now="1000")
    assert finding["explanation"] == reply
