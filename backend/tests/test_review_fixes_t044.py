"""T044 review fixes: workstation containment, simulator stepping, cost-metric edge cases."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest

from cfo.analysis.cost_metrics import PAIRS, detect_anomalies, monthly_series
from cfo.config import get_settings
from cfo.db.migrate import SCHEMA_PATH
from cfo.db.repo import Repository
from cfo.domain import CostMetric, CostMetricKind, Event, EventKind
from cfo.mcp_servers.workstations import DbAccessLog, WorkstationTools
from cfo.sim.engine import Simulator

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
        db_log = DbAccessLog(lambda: Repository.connect(isolated))
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
# simulator
class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def txn_event(n: int, ts: datetime) -> Event:
    return Event(
        id=f"evt_u{n}", ts=ts, kind=EventKind.BANK_TRANSACTION,
        payload={"transaction": {"id": f"txn_u{n}", "booking_date": ts.date().isoformat(), "type": "SUPPLIER PAYMENT",
                                 "reference": f"Invoice AS-{n}", "counterparty": "Acme", "amount": "-10.00"}},
    )


@pytest.fixture
def sim(request):
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    schema = f"{SCHEMA}_sim"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute(f"CREATE SCHEMA {schema}")
    isolated = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}"
    with psycopg.connect(isolated) as c:
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    repo = Repository.connect(isolated)
    t = datetime(2025, 12, 1, 8, tzinfo=UTC)
    events = [txn_event(1, t), txn_event(2, t), txn_event(3, t), txn_event(4, datetime(2025, 12, 2, tzinfo=UTC))]
    clock = FakeClock()
    got: list[Event] = []
    s = Simulator(repo, events, speed=3600.0, clock=clock, subscribers=[got.append])
    s.got, s.fake = got, clock  # type: ignore[attr-defined]
    yield s
    repo.conn.close()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_step_advances_exactly_one_event_even_on_timestamp_ties(sim):
    assert sim.step().id == "evt_u1" and sim.status().emitted == 1
    assert sim.step().id == "evt_u2" and sim.status().emitted == 2
    assert sim.step().id == "evt_u3"
    assert [e.id for e in sim.got] == ["evt_u1", "evt_u2", "evt_u3"]


def test_step_on_running_playback_does_not_flush_overdue_events(sim):
    sim.start()
    sim.fake.advance(30)  # 30 sim hours: events 1-3 (08:00) are overdue, event 4 (02.12 00:00) too
    assert sim.step().id == "evt_u1"
    assert sim.status().emitted == 1 and [e.id for e in sim.got] == ["evt_u1"]
    assert [e.id for e in sim.tick()] == ["evt_u2", "evt_u3", "evt_u4"]  # tick still catches up


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 0, -1.0])
def test_start_rejects_non_finite_or_non_positive_speed(sim, bad):
    with pytest.raises(ValueError):
        sim.start(bad)
    assert sim.status().paused  # nothing was changed


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0])
def test_set_speed_rejects_non_finite_or_non_positive_speed(sim, bad):
    with pytest.raises(ValueError):
        sim.set_speed(bad)


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
def test_rejected_start_speed_has_no_side_effects_on_a_running_simulator(sim):
    sim.start()
    sim.fake.advance(30)  # events are overdue
    with pytest.raises(ValueError):
        sim.start(float("nan"))
    assert sim.status().emitted == 0 and sim.got == []
    assert [e.id for e in sim.tick()][:1] == ["evt_u1"]


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
# second Codex review of T044
@pytest.fixture
def repo():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    schema = f"{SCHEMA}_repo"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        c.execute(f"CREATE SCHEMA {schema}")
    isolated = f"{url}{'&' if '?' in url else '?'}options=-csearch_path%3D{schema}"
    with psycopg.connect(isolated) as c:
        c.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    r = Repository.connect(isolated)
    yield r
    r.conn.close()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_cost_from_zero_anomaly_round_trips_through_the_repository(repo):
    from cfo.agent.investigate import build_investigation_graph
    from cfo.domain import Decision, PolicyLevel

    rows = [cm(CLOUD, 5, 0, 0), cm(CLOUD, 6, 150, 0)]

    class Gw:
        def cost_metrics(self):
            return [r.model_dump(mode="json") for r in rows]

    class Llm:
        def chat(self, messages, **kw):
            raise RuntimeError("no llm")  # explain() falls back to the code-made sentence

    (f,) = build_investigation_graph(Gw(), Llm()).invoke({})["findings"]
    assert f["facts"]["cost_change_pct"] is None and "new cost" in f["summary"]
    dec = Decision(action_type="alert_cost_anomaly", level=PolicyLevel.NOTIFY, reason="r", summary=f["summary"],
                   payload={"kind": f["kind"], "month": f["month"], "facts": f["facts"]})
    repo.add_decision(dec)  # Postgres rejects Infinity/NaN in JSONB
    assert repo.list_decisions()[0].payload["facts"]["cost_change_pct"] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -2.0])
def test_start_without_speed_validates_the_default_before_persisting_state(sim, bad):
    sim.default_speed = bad  # as if SIM_SPEED were bad; no sim_state row exists yet
    with pytest.raises(ValueError):
        sim.start()
    assert sim.repo.conn.execute("SELECT count(*) AS n FROM sim_state").fetchone()["n"] == 0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0, -2.0])
def test_start_without_speed_validates_the_stored_speed(sim, bad):
    sim.status()  # creates the row
    sim.repo.conn.execute("UPDATE sim_state SET speed = %s", (bad,))
    sim.repo.conn.commit()
    with pytest.raises(ValueError):
        sim.start()
    assert sim.status().paused


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
