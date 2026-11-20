"""T049: the cost-vs-metric alert template always states the cost change (percent, EUR before -> now), the metric
change and the cost per unit in EUR. No database.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cfo.analysis.cost_metrics import detect_anomalies
from cfo.domain import CostMetric, CostMetricKind

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
