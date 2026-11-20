"""Synthetic data: determinism, consistency with the bank statement, scenario cases, seeding."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import date
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from pathlib import Path

import psycopg
import pytest
from cfo.config import get_settings
from cfo.data import load, people
from cfo.data.generate import generate
from cfo.data.source import SourceNotFound, find_source_csv, load_bank_csv
from cfo.db.repo import Repository
from cfo.domain import Event, Invoice, Transaction

try:
    SOURCE = find_source_csv()
except (
    SourceNotFound
):  # organizer material is git-excluded; set SOURCE_CSV to run these tests
    pytest.skip(
        "bank statement CSV not available (set SOURCE_CSV)", allow_module_level=True
    )


@pytest.fixture(scope="module")
def out(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("gen") / "generated"
    generate(path, seed=42)
    return path


def jl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def invoices(out: Path) -> dict[str, Invoice]:
    return {
        p.stem: Invoice(**json.loads(p.read_text()))
        for p in (out / "invoices" / "json").glob("*.json")
    }


# -- statement ----------------------------------------------------------------------------------------------
def test_statement_loaded_unchanged():
    txns = load_bank_csv(SOURCE)
    assert len(txns) == 1125
    assert (
        txns[0].booking_date <= txns[-1].booking_date
    )  # chronological, though the file is newest-first
    assert sum(t.amount for t in txns) == Decimal(
        "32945.51"
    )  # closing balance of the statement
    running, low = Decimal(0), Decimal(0)
    for t in txns:
        running += t.amount
        low = min(low, running)
    assert (
        Decimal("-26000") < low < Decimal("-20000")
    )  # the June hole the agent must foresee
    assert len({t.id for t in txns}) == 1125


def test_deterministic_and_seed_sensitive(out, tmp_path):
    again = tmp_path / "again"
    generate(again, seed=42)
    assert tree_hash(again) == tree_hash(out)
    other = tmp_path / "other"
    generate(other, seed=7)
    assert tree_hash(other) != tree_hash(out)


def test_history_december_split(out):
    history = [Transaction(**r) for r in jl(out / "bank" / "history.jsonl")]
    events = [Event(**r) for r in jl(out / "events" / "december.jsonl")]
    bank = [e for e in events if e.kind == "bank_transaction"]
    assert all(t.booking_date.month <= 11 for t in history)
    assert len(history) + len(bank) == 1125
    assert all(e.ts.month == 12 for e in events)
    assert [e.ts for e in events] == sorted(e.ts for e in events)
    assert len({e.id for e in events}) == len(events)


# -- invoices -------------------------------------------------------------------------------------------------
def test_invoices_match_payments_to_the_cent(out):
    txns = {t.id: t for t in load_bank_csv(SOURCE)}
    invs = invoices(out)
    linked = 0
    for inv in invs.values():
        assert sum(ln.amount for ln in inv.lines) == inv.total
        if inv.transaction_id:
            linked += 1
            assert inv.total == -txns[inv.transaction_id].amount
            assert txns[inv.transaction_id].invoice_number == inv.number
    assert linked > 200
    assert len(list((out / "invoices" / "pdf").glob("*.pdf"))) == len(
        [n for n in invs if n.startswith(("AS-", "FLV-"))]
    )


def test_scenario_cases_are_disjoint_and_complete(out):
    sc = json.loads((out / "scenario.json").read_text())
    h, d = sc["history"], sc["december"]
    numbers = [
        x["invoice_number"] for x in h["workstation_downloads"] + h["workstation_mail"]
    ] + [
        h["mismatch"]["invoice_number"],
        h["missing_everywhere"][0]["invoice_number"],
        d["workstation"]["invoice_number"],
        d["missing_everywhere"]["invoice_number"],
    ]
    assert len(numbers) == len(set(numbers)) == 3 + 2 + 1 + 1 + 1 + 1
    assert d["workstation"]["invoice_number"] == "AS-69664"
    invs = invoices(out)
    assert not set(numbers) & set(invs)  # none of them is in the database
    all_payments = {t.invoice_number for t in load_bank_csv(SOURCE) if t.invoice_number}
    assert set(numbers) <= all_payments
    assert all_payments - set(numbers) == {
        n for n in invs if n.startswith(("AS-", "FLV-"))
    }
    # every December payment except the two special ones has its invoice waiting in the database
    dec_numbers = {
        t.invoice_number
        for t in load_bank_csv(SOURCE)
        if t.invoice_number and t.booking_date.month == 12
    }
    assert set(d["documented_in_db"]) == dec_numbers - {"AS-69664", "FLV-42026"}


def test_workstation_files(out):
    sc = json.loads((out / "scenario.json").read_text())
    ws = out / "workstations" / "grazyna"
    assert {p.name for p in ws.iterdir()} == {
        "Downloads",
        "Mail",
        "Documents",
        "Private",
    }
    for item in sc["history"]["workstation_downloads"]:
        raw = (ws / item["file"]).read_bytes()
        assert raw.startswith(b"%PDF") and item["invoice_number"].encode() in raw
        assert item["total"].encode() in raw
    for item in sc["history"]["workstation_mail"]:
        msg = BytesParser(policy=policy.default).parsebytes(
            (ws / item["file"]).read_bytes()
        )
        assert item["invoice_number"] in msg["Subject"]
        pdfs = [
            a
            for a in msg.iter_attachments()
            if a.get_content_type() == "application/pdf"
        ]
        assert len(pdfs) == 1 and item["total"].encode() in pdfs[0].get_content()
        assert item["total"] in msg.get_body().get_content()
    mm = sc["history"]["mismatch"]
    raw = (ws / mm["file"]).read_bytes()
    assert mm["invoice_total"].encode() in raw and mm["paid"] != mm["invoice_total"]
    dec = sc["december"]["workstation"]
    assert dec["total"].encode() in (ws / dec["file"]).read_bytes()


@pytest.mark.skipif(shutil.which("pdftotext") is None, reason="pdftotext not installed")
def test_pdf_is_readable(out):
    text = subprocess.run(
        [
            "pdftotext",
            "-layout",
            str(out / "workstations/grazyna/Downloads/AS-69664.pdf"),
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "INVOICE AS-69664" in text and "623.48" in text


# -- people ---------------------------------------------------------------------------------------------------
def test_hr(out):
    emps = {e.id: e for e in people.parse_hr_csv((out / "hr" / "hr.csv").read_text())}
    assert {"vincenzo", "grazyna", "rosa", "yusuf", "vanessa"} == set(emps)
    assert emps["vincenzo"].manager_id is None and emps["vincenzo"].role.startswith(
        "CEO"
    )
    assert (
        "office manager" in emps["grazyna"].role.lower()
        and "head chef" in emps["rosa"].role.lower()
    )
    assert emps["grazyna"].on_vacation(date(2025, 12, 29)) and emps[
        "grazyna"
    ].on_vacation(date(2025, 12, 30))
    assert not emps["grazyna"].on_vacation(date(2025, 12, 5))
    for e in emps.values():  # every chain ends at the CEO
        seen = e
        for _ in range(5):
            if seen.manager_id is None:
                break
            seen = emps[seen.manager_id]
        assert seen.id == "vincenzo"
    teams = {t["id"]: t for t in json.loads((out / "teams.json").read_text())}
    assert set(teams) == {"kitchen", "front", "marketing", "it", "equipment"}
    assert teams["kitchen"]["owner_id"] == "rosa"


# -- cost vs metric pairs -------------------------------------------------------------------------------------
def test_cost_metrics_and_cloud_spike(out):
    history = json.loads((out / "metrics" / "cost_metrics.json").read_text())
    assert len(history) == 33 and {m["kind"] for m in history} == {
        "food_cost",
        "ads_cac",
        "cloud_per_order",
    }
    assert all(m["month"] <= "2025-11-01" for m in history)
    cloud = {m["month"]: m for m in history if m["kind"] == "cloud_per_order"}
    nov_cost, nov_orders = Decimal(cloud["2025-11-01"]["cost"]), Decimal(
        cloud["2025-11-01"]["metric_value"]
    )
    for earlier, later in zip(
        sorted(cloud), sorted(cloud)[1:]
    ):  # no spike during history
        assert Decimal(cloud[later]["cost"]) / Decimal(
            cloud[earlier]["cost"]
        ) < Decimal("1.3")
    events = [Event(**r) for r in jl(out / "events" / "december.jsonl")]
    metric = {e.payload["metric"]: e.payload for e in events if e.kind == "metric"}
    dec = metric["cloud_invoice"]
    cost, orders = Decimal(dec["cost_metric"]["cost"]), Decimal(
        dec["cost_metric"]["metric_value"]
    )
    assert Decimal("1.49") <= cost / nov_cost <= Decimal("1.51")
    assert Decimal("1.02") <= orders / nov_orders <= Decimal("1.04")
    inv = Invoice(**dec["invoice"])
    assert inv.total == cost and sum(ln.amount for ln in inv.lines) == cost
    assert {r["ServiceName"] for r in dec["focus"]} >= {
        "Compute (app backend)",
        "Log ingestion and retention",
    }
    assert "BilledCost" in dec["focus"][0] and "ServiceCategory" in dec["focus"][0]
    assert {"ad_spend", "cloud_invoice", "monthly_orders"} == set(metric)


def test_one_equipment_purchase(out):
    eq = [i for i in invoices(out).values() if i.category == "equipment"]
    assert len(eq) == 1 and eq[0].team_id == "equipment" and eq[0].total > 1000


# -- database -------------------------------------------------------------------------------------------------
@pytest.fixture
def repo():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    schema = f"t002_test_{os.getpid()}"
    r = Repository.connect(url)
    r.conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    r.conn.execute(f"CREATE SCHEMA {schema}")
    r.conn.execute(f"SET search_path TO {schema}")
    r.conn.commit()
    from cfo.db.migrate import SCHEMA_PATH

    r.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    r.conn.commit()
    yield r
    r.conn.rollback()
    r.conn.execute(f"DROP SCHEMA {schema} CASCADE")
    r.conn.commit()
    r.conn.close()


def test_seed_and_december_replay(out, repo):
    counts = load.seed(repo, out)
    assert counts["transactions"] == 1022 and counts["employees"] == 5
    assert repo.get_employee("grazyna").on_vacation(date(2025, 12, 30))
    assert repo.get_employee("grazyna").manager_id == "vincenzo"
    sc = json.loads((out / "scenario.json").read_text())["history"]
    expected = {
        x["transaction_id"]
        for x in sc["workstation_downloads"] + sc["workstation_mail"]
    } | {
        sc["mismatch"]["transaction_id"],
        sc["missing_everywhere"][0]["transaction_id"],
    }
    assert {t.id for t in repo.list_transactions(undocumented_only=True)} == expected
    assert len(repo.list_cost_metrics("cloud_per_order")) == 11
    load.seed(repo, out)  # idempotent
    assert len(repo.list_transactions()) == 1022

    for event in load.load_events(out):
        load.apply_event(repo, event)
    undocumented = {
        t.invoice_number
        for t in repo.list_transactions(since=date(2025, 12, 1), undocumented_only=True)
    }
    assert undocumented == {"AS-69664", "FLV-42026"}
    assert len(repo.list_cost_metrics("cloud_per_order")) == 12
    assert repo.get_invoice_by_number("CLD-2025-12") is not None
