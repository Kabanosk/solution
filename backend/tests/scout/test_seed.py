from datetime import datetime, timezone
from pathlib import Path

import pytest

from cfo.data.source import SourceNotFound, find_source_csv
from scout.seed import plant_demo_evidence, seed


@pytest.fixture
def csv_path():
    try:
        return find_source_csv()
    except SourceNotFound:
        pytest.skip("organizer CSV not available")


def test_seed_history_ends_before_today(conn_factory, db, csv_path, tmp_path):
    now = datetime.now(timezone.utc)
    stats = seed(conn_factory, data_dir=tmp_path, csv=csv_path, now=now)
    assert stats["transactions"] >= 1000
    last = db.execute("SELECT max(booking_date) AS d FROM transactions").fetchone()["d"]
    assert last < now.date()
    assert db.execute("SELECT count(*) AS n FROM employees").fetchone()["n"] == 5
    kinds = {r["kind"] for r in db.execute("SELECT kind FROM connections").fetchall()}
    assert {"bank", "invoicing", "mail", "chat", "files"} <= kinds
    undocumented = db.execute(
        "SELECT count(*) AS n FROM transactions WHERE invoice_number IS NOT NULL AND NOT documented").fetchone()["n"]
    assert undocumented == 0
    latest = db.execute("SELECT max(month) AS m FROM cost_metrics").fetchone()["m"]
    assert latest.year == now.year and latest.month in (now.month, now.month - 1)


def test_grazyna_on_vacation_today(conn_factory, db, csv_path, tmp_path):
    now = datetime.now(timezone.utc)
    seed(conn_factory, data_dir=tmp_path, csv=csv_path, now=now)
    row = db.execute("SELECT count(*) AS n FROM vacations WHERE employee_id='grazyna' AND start_date <= %s "
                     "AND end_date >= %s", (now.date(), now.date())).fetchone()
    assert row["n"] == 1


def test_plant_demo_evidence(tmp_path):
    placed = plant_demo_evidence(tmp_path, datetime.now(timezone.utc).date())
    for rel in placed.values():
        assert (tmp_path / rel).is_file()
    assert (tmp_path / "workstations/grazyna/Private/personal-notes.txt").is_file()
