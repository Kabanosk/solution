"""T044 review fixes: workstation containment and cost-metric edge cases."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import psycopg
from api_test_helpers import operator_repository
import pytest

from cfo.analysis.cost_metrics import PAIRS, detect_anomalies, monthly_series
from cfo.config import get_settings
from cfo.db.migrate import SCHEMA_PATH
from cfo.db.repo import Repository
from cfo.domain import CostMetric, CostMetricKind
from cfo.mcp_servers.workstations import DbAccessLog, WorkstationTools

CLOUD = CostMetricKind.CLOUD_PER_ORDER
SCHEMA = f"t044_test_{os.getpid()}"


# ---------------------------------------------------------------------------------------------------
# workstations (no database)
class MemoryLog:
    def __init__(self):
        self.entries: list[tuple] = []

    def log(self, employee, action, path, reason):
        self.entries.append((employee, action, path, reason))


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "w"
    g = root / "ann"
    for d in ("Downloads", "Mail", "Documents", "Private"):
        (g / d).mkdir(parents=True)
    (g / "Private" / "diary.txt").write_text("secret", encoding="utf-8")
    (g / "Documents" / "ok.txt").write_text("fine", encoding="utf-8")
    log = MemoryLog()
    return WorkstationTools(root, log), log, g


def test_company_folder_symlink_to_private_sibling_is_denied_and_logged(ws):
    tools, log, g = ws
    (g / "Mail").rmdir()
    (g / "Mail").symlink_to(g / "Private")
    res = tools.read_file("ann", "Mail/diary.txt")
    assert res["error"] == "access_denied"
    assert tools.list_files("ann", "Mail")["error"] == "access_denied"
    assert tools.search_files("ann", "secret", folder="Mail")["error"] == "access_denied"
    assert len(log.entries) == 3 and all(e[1].startswith("DENIED") for e in log.entries)


def test_symlink_inside_folder_to_private_sibling_is_denied(ws):
    tools, _, g = ws
    (g / "Downloads" / "sub").symlink_to(g / "Private")
    assert tools.read_file("ann", "Downloads/sub/diary.txt")["error"] == "access_denied"


def test_normal_company_file_still_readable(ws):
    tools, _, _ = ws
    assert tools.read_file("ann", "Documents/ok.txt")["content"] == "fine"


def test_list_workstations_is_logged(ws):
    tools, log, _ = ws
    assert [w["employee"] for w in tools.list_workstations()] == ["ann"]
    assert len(log.entries) == 1 and log.entries[0][1] == "list_workstations"


def test_list_workstations_hides_symlinked_company_folder(ws):
    tools, _, g = ws
    (g / "Mail").rmdir()
    (g / "Mail").symlink_to(g / "Private")
    assert tools.list_workstations()[0]["folders"] == ["Downloads", "Documents"]


@pytest.mark.parametrize("call", [
    lambda t: t.read_file("ann", "Documents/ok\x00.txt", reason="r\x00x"),
    lambda t: t.list_files("ann", "Documents\x00"),
    lambda t: t.search_files("ann", "q\x00", folder="Documents\x00"),
    lambda t: t.read_file("ann\x00", "Documents/ok.txt"),
    lambda t: t.read_file("ann\n", "Documents/ok.txt"),
])
def test_nul_is_denied_and_logged_without_nul(ws, call):
    tools, log, _ = ws
    res = call(tools)
    assert res["error"] == "access_denied"
    assert log.entries and all("\x00" not in str(field) for e in log.entries for field in e)


def test_nul_denial_reaches_the_real_access_log(ws):
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.execute(f"CREATE SCHEMA {SCHEMA}")
    isolated = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{SCHEMA}"
    try:
        with psycopg.connect(isolated) as c:
            c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        tools, _, _ = ws
        db_log = DbAccessLog(lambda: operator_repository(isolated))
        tools.access_log = db_log
        assert tools.read_file("ann", "Documents/x\x00")["error"] == "access_denied"
        tools.list_workstations()
        rows = db_log.entries()
        assert [r["action"] for r in rows] == ["DENIED read_file", "list_workstations"]
        assert "\x00" not in rows[0]["path"]
    finally:
        with psycopg.connect(url, autocommit=True) as c:
            c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


# ---------------------------------------------------------------------------------------------------
# cost metrics
def cm(kind, month, cost, metric, year=2025):
    return CostMetric(kind=kind, month=date(year, month, 1), team_id=PAIRS[kind].team_id, cost=Decimal(str(cost)),
                      metric_value=Decimal(str(metric)), detail={})


def test_cost_from_zero_with_zero_metric_is_flagged():
    rows = [cm(CLOUD, 5, 0, 0), cm(CLOUD, 6, 150, 0)]
    (a,) = detect_anomalies(rows)
    assert a.month == date(2025, 6, 1) and a.cost == 150 and a.cost_change_pct is None
    assert a.cost_per_unit is None
    assert "new cost" in a.explanation and "EUR 0.00" in a.explanation and "EUR 150.00" in a.explanation


def test_cost_rising_while_metric_stays_at_zero_is_flagged():
    rows = [cm(CLOUD, 5, 100, 0), cm(CLOUD, 6, 150, 0)]
    (a,) = detect_anomalies(rows)
    assert a.cost_change_pct == 50 and a.metric_change_pct == 0 and "did not change" in a.explanation


def test_cost_up_with_metric_restarting_from_zero_is_not_flagged():
    rows = [cm(CLOUD, 5, 0, 0), cm(CLOUD, 6, 22800, 2300)]  # reopening after a shutdown
    assert detect_anomalies(rows) == []


def test_flat_zero_cost_and_zero_metric_is_not_flagged():
    assert detect_anomalies([cm(CLOUD, 5, 0, 0), cm(CLOUD, 6, 0, 0)]) == []


def test_missing_month_is_not_month_over_month():
    rows = [cm(CLOUD, 4, 100, 100), cm(CLOUD, 6, 200, 100)]  # May is missing
    assert detect_anomalies(rows) == []
    p = monthly_series(rows)[0].points
    assert p[1].cost_change_pct is None and p[1].metric_change_pct is None and p[1].anomaly is None


def test_consecutive_months_across_the_year_boundary_are_compared():
    rows = [cm(CLOUD, 12, 100, 100, year=2024), cm(CLOUD, 1, 200, 100, year=2025)]
    (a,) = detect_anomalies(rows)
    assert a.month == date(2025, 1, 1)


# ---------------------------------------------------------------------------------------------------
# Codex review of T044
def test_cyclic_company_folder_symlink_is_denied_and_logged(ws):
    tools, log, g = ws
    (g / "Mail").rmdir()
    (g / "Mail").symlink_to(g / "Mail")  # loop
    assert tools.read_file("ann", "Mail/x.txt")["error"] == "access_denied"
    assert tools.list_files("ann", "Mail")["error"] == "access_denied"
    assert [e[1] for e in log.entries] == ["DENIED read_file", "DENIED list_files"]
    assert tools.list_workstations()[0]["folders"] == ["Downloads", "Documents"]  # does not raise


def test_cyclic_symlink_inside_a_company_folder_is_denied(ws):
    tools, _, g = ws
    (g / "Downloads" / "loop").symlink_to(g / "Downloads" / "loop")
    assert tools.read_file("ann", "Downloads/loop/x.txt")["error"] == "access_denied"


def test_zero_to_zero_metric_respects_the_growth_cap():
    rows = [cm(CLOUD, 5, 100, 0), cm(CLOUD, 6, 150, 0)]
    assert len(detect_anomalies(rows)) == 1
    assert detect_anomalies(rows, metric_growth_max_pct=0) == []  # 0% growth is not below a cap of 0


# ---------------------------------------------------------------------------------------------------
# third Codex review of T044
def test_employee_directory_symlink_to_another_workstation_is_denied_and_logged(ws):
    tools, log, g = ws
    (g.parent / "bob").symlink_to(g)  # bob -> ann
    for call in (lambda: tools.read_file("bob", "Documents/ok.txt"), lambda: tools.list_files("bob", "Documents"),
                 lambda: tools.search_files("bob", "fine")):
        assert call()["error"] == "access_denied"
    assert [e[1] for e in log.entries] == ["DENIED read_file", "DENIED list_files", "DENIED search_files"]
    assert [w["employee"] for w in tools.list_workstations()] == ["ann"]
    assert tools.read_file("ann", "Documents/ok.txt")["ok"]
