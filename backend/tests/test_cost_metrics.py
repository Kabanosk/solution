"""Cost vs metric analysis (T007): series, per-unit cost, anomaly detection for the three D-006 pairs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from cfo.analysis.cost_metrics import (
    PAIRS, detect_anomalies, line_items, load_series, monthly_series, to_api_series,
)
from cfo.api import stubs
from cfo.api.schemas import Collection, CostMetricSeries
from cfo.domain import CostMetric, CostMetricKind, Invoice, InvoiceLine

CLOUD, FOOD, ADS = CostMetricKind.CLOUD_PER_ORDER, CostMetricKind.FOOD_COST, CostMetricKind.ADS_CAC


def cm(kind, month, cost, metric, **detail):
    return CostMetric(kind=kind, month=date(2025, month, 1), team_id=PAIRS[kind].team_id, cost=Decimal(str(cost)),
                      metric_value=Decimal(str(metric)), detail=detail)


def cloud_lines(compute, logs):
    return {"lines": [{"service": "Compute", "cost": str(compute)}, {"service": "Log ingestion", "cost": str(logs)}]}


# -- synthetic cases (no data files needed) ---------------------------------------------------------------
def test_cloud_plus_50_percent_with_flat_orders_is_flagged_with_top_line():
    rows = [cm(CLOUD, 11, 244, 278, **cloud_lines(200, 44)), cm(CLOUD, 12, 366, 286, **cloud_lines(210, 156))]
    (a,) = detect_anomalies(rows)
    assert a.kind == CLOUD and a.month == date(2025, 12, 1) and a.team_id == "it"
    assert round(a.cost_change_pct, 1) == Decimal("50.0") and round(a.metric_change_pct, 1) == Decimal("2.9")
    assert [d.label for d in a.drivers] == ["Log ingestion", "Compute"]  # biggest increase first
    assert a.drivers[0].previous == 44 and a.drivers[0].current == 156
    assert round(a.previous_cost_per_unit, 2) == Decimal("0.88") and round(a.cost_per_unit, 2) == Decimal("1.28")
    assert "rose 50.0%" in a.explanation and "online orders grew only 2.9%" in a.explanation
    assert "Log ingestion went from EUR 44.00 to EUR 156.00" in a.explanation
    assert a.explanation.count(". ") == 0 and a.explanation.endswith(".")  # one sentence


@pytest.mark.parametrize("cost, metric", [(250, 300), (366, 330), (260, 278)])
def test_cost_that_follows_the_metric_or_grows_slowly_is_not_flagged(cost, metric):
    rows = [cm(CLOUD, 11, 244, 278), cm(CLOUD, 12, cost, metric)]
    assert detect_anomalies(rows) == []


def test_thresholds_are_parameters():
    rows = [cm(CLOUD, 11, 100, 100), cm(CLOUD, 12, 115, 100)]
    assert detect_anomalies(rows) == []
    assert len(detect_anomalies(rows, cost_growth_pct=10)) == 1


def test_zero_months_never_divide_and_are_not_flagged():
    rows = [cm(FOOD, 5, 24000, 2100), cm(FOOD, 6, 0, 0), cm(FOOD, 7, 22800, 2300)]  # June shutdown
    s = monthly_series(rows)[0]
    assert [p.cost_per_unit is None for p in s.points] == [False, True, False]
    assert s.points[2].cost_change_pct is None and s.points[2].metric_change_pct is None
    assert detect_anomalies(rows) == []


def test_cost_with_no_metric_at_all_is_flagged_without_per_unit():
    rows = [cm(CLOUD, 5, 200, 100), cm(CLOUD, 6, 300, 0)]
    (a,) = detect_anomalies(rows)
    assert a.cost_per_unit is None and a.metric_change_pct == Decimal(-100) and "fell 100.0%" in a.explanation


def test_each_pair_has_its_own_wording_and_line_source():
    inv = Invoice(number="ADS-2025-12", supplier="Meridian", issue_date=date(2025, 12, 31), total=Decimal(1200),
                  lines=[InvoiceLine(description="Social ads", unit_price=Decimal(500), amount=Decimal(500)),
                         InvoiceLine(description="Search ads", unit_price=Decimal(700), amount=Decimal(700))])
    prev_inv = Invoice(number="ADS-2025-11", supplier="Meridian", issue_date=date(2025, 11, 30), total=Decimal(800),
                       lines=[InvoiceLine(description="Social ads", unit_price=Decimal(500), amount=Decimal(500)),
                              InvoiceLine(description="Search ads", unit_price=Decimal(300), amount=Decimal(300))])
    ads = [cm(ADS, 11, 800, 60, invoice_number="ADS-2025-11"), cm(ADS, 12, 1200, 60, invoice_number="ADS-2025-12")]
    food = [cm(FOOD, 11, 22000, 2000, by_supplier={"Asimex": "9000", "Fattoria": "13000"}),
            cm(FOOD, 12, 29000, 2050, by_supplier={"Asimex": "9100", "Fattoria": "19900"})]
    found = detect_anomalies(ads + food, [inv, prev_inv])
    by_kind = {a.kind: a for a in found}
    assert set(by_kind) == {ADS, FOOD}
    assert by_kind[ADS].drivers[0].label == "Search ads" and by_kind[ADS].team_id == "marketing"
    assert "Ad spend rose 50.0%" in by_kind[ADS].explanation and "new customers did not change" in by_kind[ADS].explanation
    assert "cost per new customer (CAC) from EUR 13.33 to EUR 20.00" in by_kind[ADS].explanation
    assert by_kind[FOOD].drivers[0].label == "Fattoria" and by_kind[FOOD].drivers[0].delta == Decimal(6900)
    assert "food cost per order" in by_kind[FOOD].explanation


def test_line_items_ignore_invoice_number_and_junk():
    m = cm(ADS, 3, 10, 1, invoice_number="123", note="n/a", lines="oops")
    assert line_items(m) == {}


def test_drivers_only_list_lines_that_rose_and_are_capped():
    before = {"lines": [{"service": f"s{i}", "cost": 10} for i in range(6)] + [{"service": "dropped", "cost": 50}]}
    after = {"lines": [{"service": f"s{i}", "cost": 10 + 5 * i} for i in range(6)] + [{"service": "dropped", "cost": 1}]}
    (a,) = detect_anomalies([cm(CLOUD, 1, 110, 100, **before), cm(CLOUD, 2, 161, 100, **after)])
    assert [d.label for d in a.drivers] == ["s5", "s4", "s3"]


def test_series_are_ordered_by_pair_and_month_regardless_of_input_order():
    rows = [cm(CLOUD, 2, 2, 1), cm(ADS, 2, 2, 1), cm(FOOD, 2, 2, 1), cm(CLOUD, 1, 1, 1), cm(FOOD, 1, 1, 1)]
    series = monthly_series(rows)
    assert [s.kind for s in series] == [FOOD, ADS, CLOUD]
    assert [p.month.month for p in series[0].points] == [1, 2]
    assert series[0].points[0].cost_change_pct is None and series[0].points[1].cost_change_pct == Decimal(100)


def test_api_shape_matches_cost_metrics_endpoint():
    rows = [cm(CLOUD, 11, 244, 278, **cloud_lines(200, 44)), cm(CLOUD, 12, 366, 286, **cloud_lines(210, 156))]
    items = to_api_series(monthly_series(rows))
    page = Collection[CostMetricSeries](items=items)  # validates against the response model of T018
    (s,) = page.items
    assert (s.kind, s.team_id, s.unit, s.label) == (CLOUD, "it", "online_order", "App cloud vs online orders")
    nov, dec = s.points
    assert nov.cost_change_pct is None and nov.anomaly is None
    assert dec.cost.amount == "366.00" and dec.cost_per_unit.amount == "1.28" and dec.metric_value == "286"
    assert dec.cost_change_pct == "50.0" and dec.metric_change_pct == "2.9"
    assert dec.anomaly.drivers[0].label == "Log ingestion"
    assert dec.anomaly.drivers[0].previous.amount == "44.00" and dec.anomaly.drivers[0].current.amount == "156.00"


class FakeRepo:
    def __init__(self, metrics, invoices):
        self.metrics, self.invoices = metrics, {i.number: i for i in invoices}

    def list_cost_metrics(self, kind=None):
        return self.metrics

    def get_invoice_by_number(self, number):
        return self.invoices.get(number)


def test_load_series_reads_metrics_and_referenced_invoices_from_a_repository():
    inv = lambda n, social, search: Invoice(  # noqa: E731
        number=n, supplier="M", issue_date=date(2025, 12, 31), total=Decimal(social + search),
        lines=[InvoiceLine(description="Social", unit_price=Decimal(social), amount=Decimal(social)),
               InvoiceLine(description="Search", unit_price=Decimal(search), amount=Decimal(search))])
    repo = FakeRepo([cm(ADS, 11, 800, 60, invoice_number="A11"), cm(ADS, 12, 1200, 60, invoice_number="A12")],
                    [inv("A11", 500, 300), inv("A12", 500, 700)])
    (s,) = load_series(repo)
    assert s.points[-1].anomaly.drivers[0].label == "Search"


# -- the generated company (T002 data; needs the organizer's bank statement like the other data tests) ------
@pytest.fixture(scope="module")
def generated():
    from cfo.data.metrics import build_months
    from cfo.data.source import SourceNotFound, load_bank_csv

    try:
        months = build_months(load_bank_csv(), seed=42)
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")
    metrics = [c for m in months for c in m.cost_metrics]
    invoices = [i for m in months for i in (m.ads_invoice, m.cloud_invoice)]
    return metrics, invoices


def test_generated_year_flags_only_the_december_cloud_invoice(generated):
    metrics, invoices = generated
    (a,) = detect_anomalies(metrics, invoices)
    assert (a.kind, a.month) == (CLOUD, date(2025, 12, 1))
    assert round(a.cost_change_pct, 1) == Decimal("50.0") and round(a.metric_change_pct, 1) == Decimal("2.9")
    assert a.drivers[0].label == "Log ingestion and retention"
    assert (a.drivers[0].previous, a.drivers[0].current) == (Decimal("26.95"), Decimal("147.23"))
    assert [round(x, 2) for x in (a.previous_cost_per_unit, a.cost_per_unit)] == [Decimal("0.88"), Decimal("1.28")]


def test_generated_year_has_three_full_series_and_june_without_per_unit_cost(generated):
    metrics, invoices = generated
    series = monthly_series(metrics, invoices)
    assert [s.kind for s in series] == [FOOD, ADS, CLOUD] and all(len(s.points) == 12 for s in series)
    june = {s.kind: s.points[5] for s in series}
    assert june[FOOD].cost_per_unit is None and june[CLOUD].cost_per_unit is None  # no orders in the shutdown
    assert june[ADS].cost_per_unit is not None


def test_computed_series_equal_the_api_stub_values(generated):
    """T019 can swap the stub for this: same pairs, months, money and anomaly as the T018 stub."""
    metrics, invoices = generated
    real = {s.kind: s for s in to_api_series(monthly_series(metrics, invoices))}
    for stub in stubs.cost_metric_series():
        got = real[stub.kind]
        assert (got.label, got.team_id, got.unit) == (stub.label, stub.team_id, stub.unit)
        assert len(got.points) == len(stub.points)
        for g, s in zip(got.points, stub.points):
            assert (g.month, g.cost, g.metric_value, g.cost_per_unit) == (s.month, s.cost, s.metric_value, s.cost_per_unit)
            assert g.cost_change_pct == s.cost_change_pct and g.metric_change_pct == s.metric_change_pct
            assert (g.anomaly is None) == (s.anomaly is None)
    dec = real[CLOUD].points[-1].anomaly
    assert dec.drivers[0].label == "Log ingestion and retention"
