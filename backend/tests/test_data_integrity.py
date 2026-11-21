"""T040: data integrity of re-ingesting payments, reset and invoice attachment (findings of the Codex review).

Runs against the compose Postgres in a per-process schema; skipped when it is not reachable.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
from api_test_helpers import operator_repository
import pytest

from cfo.config import get_settings
from cfo.data import load
from cfo.data.generate import generate
from cfo.data.source import SourceNotFound, find_source_csv
from cfo.db.migrate import SCHEMA_PATH
from cfo.db.repo import Repository
from cfo.domain import Invoice, InvoiceLine, Team, Transaction
from cfo.mcp_servers.common import make_repo_factory
from cfo.mcp_servers.company_db import CompanyDbTools

SCHEMA = f"t040_test_{os.getpid()}"


@pytest.fixture
def factory():
    url = get_settings().database_url
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    admin = operator_repository(url)
    admin.conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    admin.conn.execute(f"CREATE SCHEMA {SCHEMA}")
    admin.conn.execute(f"SET search_path TO {SCHEMA}")
    admin.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    admin.conn.commit()
    yield make_repo_factory(url, schema=SCHEMA)
    admin.conn.rollback()
    admin.conn.execute(f"DROP SCHEMA {SCHEMA} CASCADE")
    admin.conn.commit()
    admin.conn.close()


@pytest.fixture
def repo(factory):
    with factory() as r:
        yield r


def txn(id: str, number: str | None = "AS-1", amount: str = "-100.00") -> Transaction:
    return Transaction(id=id, booking_date=date(2025, 12, 3), type="GIRO", reference=f"Invoice {number}",
                       counterparty="Asimex", amount=Decimal(amount), invoice_number=number)


def invoice(number: str = "AS-1", total: str = "100.00", transaction_id: str | None = None,
            id: str | None = None, desc: str = "Flour") -> Invoice:
    extra = {"id": id} if id else {}
    return Invoice(number=number, supplier="Asimex", issue_date=date(2025, 12, 1), total=Decimal(total),
                   lines=[InvoiceLine(description=desc, unit_price=Decimal(total), amount=Decimal(total))],
                   transaction_id=transaction_id, **extra)


# -- item 9: replay keeps reconciliation ---------------------------------------------------------------------
def test_readding_a_transaction_keeps_documented_and_invoice(repo: Repository):
    t = txn("t1")
    repo.add_transaction(t)
    repo.add_invoice(invoice(transaction_id="t1", id="inv_1"))
    assert repo.get_transaction("t1").documented is True

    repo.add_transaction(t)  # replay of the very same bank line (documented=False in the model)
    got = repo.get_transaction("t1")
    assert got.documented is True and got.invoice_id == "inv_1"


def test_reingested_payment_keeps_reconciliation(repo: Repository):
    t = txn("t1")
    repo.add_invoice(invoice(id="inv_1"))  # invoice first, payment arrives later
    first = load.ingest_transaction(repo, t)
    assert first.documented is True
    again = load.ingest_transaction(repo, t)
    assert again.documented is True and again.invoice_id == "inv_1"
    assert repo.get_transaction("t1").documented is True


def test_readding_an_unlinked_invoice_keeps_the_payment_link(repo: Repository):
    repo.add_transaction(txn("t1"))
    repo.add_invoice(invoice(transaction_id="t1", id="inv_1"))
    repo.add_invoice(invoice(id="inv_1"))  # a seed/replay copy that does not know the link
    assert repo.get_invoice_by_number("AS-1").transaction_id == "t1"
    got = repo.get_transaction("t1")
    assert got.documented is True and got.invoice_id == "inv_1"


# -- item 11: invoice number linked to another payment --------------------------------------------------------
def test_relinking_an_invoice_undocuments_the_previous_payment(repo: Repository):
    repo.add_transaction(txn("t1"))
    repo.add_transaction(txn("t2"))
    repo.add_invoice(invoice(transaction_id="t1", id="inv_1"))
    repo.add_invoice(invoice(transaction_id="t2", id="inv_1"))
    t1, t2 = repo.get_transaction("t1"), repo.get_transaction("t2")
    assert (t1.documented, t1.invoice_id) == (False, None)
    assert (t2.documented, t2.invoice_id) == (True, "inv_1")


def test_attach_refuses_an_invoice_number_linked_to_another_payment(factory):
    with factory() as r:
        r.add_transaction(txn("t1"))
        r.add_transaction(txn("t2"))
    tools = CompanyDbTools(factory)
    lines = [{"description": "Flour", "unit_price": "100", "amount": "100"}]
    assert tools.attach_invoice("t1", "AS-1", "Asimex", "2025-12-01", "100.00", lines)["ok"] is True

    other = [{"description": "Something else", "unit_price": "100", "amount": "100"}]
    res = tools.attach_invoice("t2", "AS-1", "Other Ltd", "2025-12-02", "100.00", other)
    assert res["ok"] is False and res["error"] == "invoice_linked_to_other_transaction"
    assert res["transaction_id"] == "t1"

    inv = tools.get_invoice("AS-1")
    assert inv["transaction_id"] == "t1" and inv["supplier"] == "Asimex"
    assert [ln["description"] for ln in inv["lines"]] == ["Flour"]
    assert tools.get_transaction("t1")["documented"] is True
    t2 = tools.get_transaction("t2")
    assert t2["documented"] is False and t2["invoice_id"] is None


def test_attach_still_adopts_an_unlinked_database_invoice(factory):
    with factory() as r:
        r.add_transaction(txn("t1"))
        r.add_invoice(invoice(id="inv_1"))  # invoice first, no payment yet
    tools = CompanyDbTools(factory)
    res = tools.attach_invoice("t1", "AS-1", "Asimex", "2025-12-01", "100.00")
    assert res["ok"] is True and res["invoice_id"] == "inv_1"


def _race(calls):
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, fn):
        barrier.wait()
        results[i] = fn()

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_concurrent_attach_of_two_numbers_to_one_payment(factory):
    with factory() as r:
        r.add_transaction(txn("t1"))
    for attempt in range(5):
        with factory() as r:
            r.conn.execute("UPDATE transactions SET documented = FALSE, invoice_id = NULL")
            r.conn.execute("DELETE FROM invoices")
            r.conn.commit()
        tools = CompanyDbTools(factory)
        res = _race([
            lambda: tools.attach_invoice("t1", "AS-1", "Asimex", "2025-12-01", "100.00"),
            lambda: tools.attach_invoice("t1", "AS-2", "Asimex", "2025-12-01", "100.00"),
        ])
        assert sorted(r["ok"] for r in res) == [False, True], res
        winner = next(r for r in res if r["ok"])
        assert tools.get_transaction("t1")["invoice_id"] == winner["invoice_id"]


def test_concurrent_attach_of_one_number_to_two_payments(factory):
    with factory() as r:
        r.add_transaction(txn("t1"))
        r.add_transaction(txn("t2"))
    for attempt in range(5):
        with factory() as r:
            r.conn.execute("UPDATE transactions SET documented = FALSE, invoice_id = NULL")
            r.conn.execute("DELETE FROM invoices")
            r.conn.commit()
        tools = CompanyDbTools(factory)
        res = _race([
            lambda: tools.attach_invoice("t1", "AS-1", "Asimex", "2025-12-01", "100.00"),
            lambda: tools.attach_invoice("t2", "AS-1", "Asimex", "2025-12-01", "100.00"),
        ])
        assert sorted(r["ok"] is True for r in res) == [False, True], res
        with factory() as r:
            documented = [t.id for t in r.list_transactions() if t.documented]
            assert documented == [next(r["transaction_id"] for r in res if r["ok"])]


# -- reset ------------------------------------------------------------------------------------------------------
def test_reset_empties_the_company_tables(repo: Repository):
    repo.add_team(Team(id="kitchen", name="Kitchen"))
    repo.add_transaction(txn("t1"))
    load.reset(repo)
    assert repo.list_teams() == [] and repo.list_transactions() == []


# -- item 25: reset + load in one transaction ------------------------------------------------------------------
def test_failed_reseed_keeps_the_old_data(repo: Repository, tmp_path: Path):
    repo.add_team(Team(id="kitchen", name="Kitchen"))
    repo.add_transaction(txn("t1"))

    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "teams.json").write_text('[{"id": "bar", "name": "Bar"}]')  # teams load, HR csv is missing
    with pytest.raises(FileNotFoundError):
        load.load(repo, broken, reset_first=True)

    assert [t.id for t in repo.list_teams()] == ["kitchen"]
    assert repo.get_transaction("t1") is not None


def test_reseed_in_one_transaction_replaces_the_data(repo: Repository, tmp_path: Path):
    repo.add_team(Team(id="old", name="Old"))
    good = tmp_path / "good"
    (good / "hr").mkdir(parents=True)
    (good / "bank").mkdir()
    (good / "invoices" / "json").mkdir(parents=True)
    (good / "metrics").mkdir()
    (good / "teams.json").write_text('[{"id": "bar", "name": "Bar"}]')
    (good / "hr" / "hr.csv").write_text("")
    (good / "bank" / "history.jsonl").write_text("")
    (good / "metrics" / "cost_metrics.json").write_text("[]")
    counts = load.load(repo, good, reset_first=True)
    assert counts["teams"] == 1 and [t.id for t in repo.list_teams()] == ["bar"]


# -- P3: `generate --out` only deletes its own output ----------------------------------------------------------
def _source_or_skip() -> Path:
    try:
        return find_source_csv()
    except SourceNotFound:
        pytest.skip("bank statement CSV not available (set SOURCE_CSV)")


def test_generate_does_not_delete_unrelated_files(tmp_path: Path):
    _source_or_skip()
    out = tmp_path / "generated"
    generate(out, seed=42)
    (out / "keep.txt").write_text("mine")
    (out / "notes").mkdir()
    (out / "notes" / "todo.md").write_text("mine too")
    (out / "bank" / "mine.txt").write_text("user file inside a directory that has a generated name")
    (out / "invoices" / "json" / "stale-generated.json").write_text("{}")
    sc = json.loads((out / "scenario.json").read_text())
    sc["files"].append("invoices/json/stale-generated.json")  # a file the manifest lists goes away on the next run
    (out / "scenario.json").write_text(json.dumps(sc))
    generate(out, seed=42)
    assert (out / "keep.txt").read_text() == "mine"
    assert (out / "notes" / "todo.md").read_text() == "mine too"
    assert (out / "bank" / "mine.txt").read_text().startswith("user file")  # never delete directories by name
    assert (out / "scenario.json").exists() and (out / "bank" / "history.jsonl").exists()
    assert not (out / "invoices" / "json" / "stale-generated.json").exists()
    assert not list(out.glob(".generate-*"))  # staging directory is gone


def test_generate_manifest_cannot_escape_the_output_directory(tmp_path: Path):
    _source_or_skip()
    out = tmp_path / "generated"
    generate(out, seed=42)
    victim = tmp_path / "victim.txt"
    victim.write_text("outside")
    sc = json.loads((out / "scenario.json").read_text())
    sc["files"] += ["../victim.txt", str(victim)]
    (out / "scenario.json").write_text(json.dumps(sc))
    generate(out, seed=42)
    assert victim.read_text() == "outside"


def test_generate_validates_the_source_before_deleting(tmp_path: Path):
    out = tmp_path / "generated"
    out.mkdir()
    (out / "scenario.json").write_text(json.dumps({"files": ["bank/history.jsonl"]}))
    (out / "bank").mkdir()
    (out / "bank" / "history.jsonl").write_text("precious")
    with pytest.raises(FileNotFoundError):
        generate(out, seed=42, source=tmp_path / "missing.csv")
    bad = tmp_path / "bad.csv"
    bad.write_text("a;b\n1;2\n", encoding="utf-8")
    with pytest.raises(Exception):
        generate(out, seed=42, source=bad)
    assert (out / "bank" / "history.jsonl").read_text() == "precious"
    assert (out / "scenario.json").exists()


def test_generate_with_too_few_historical_payments_keeps_the_old_output(tmp_path: Path):
    """The CSV carries the two required December invoices but fewer than seven historical invoice payments."""
    src = _source_or_skip()
    import csv
    import io

    with src.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fields = reader.fieldnames
        rows = list(reader)
    from cfo.data.source import INVOICE_RE

    kept, history_payments = [], 0
    for row in rows:  # newest first; keep December rows, thin out the earlier invoice payments
        is_dec = row["Booking Date"][-7:-5] == "12" or row["Booking Date"][3:5] == "12" or "-12-" in row["Booking Date"]
        if INVOICE_RE.search(row["Payment Reference"]) and not is_dec:
            history_payments += 1
            if history_payments > 3:
                continue
        kept.append(row)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, delimiter=";")
    w.writeheader()
    w.writerows(kept)
    thin = tmp_path / "thin.csv"
    thin.write_text(buf.getvalue(), encoding="utf-8")

    out = tmp_path / "generated"
    out.mkdir()
    (out / "scenario.json").write_text(json.dumps({"files": ["bank/history.jsonl"]}))
    (out / "bank").mkdir()
    (out / "bank" / "history.jsonl").write_text("precious")
    with pytest.raises(Exception):
        generate(out, seed=42, source=thin)
    assert (out / "bank" / "history.jsonl").read_text() == "precious"
    assert not list(out.glob(".generate-*"))


# -- review of T040 ---------------------------------------------------------------------------------------------
def test_replay_never_changes_the_invoice_of_a_documented_payment(repo: Repository):
    t = txn("t1", number="AS-1")
    repo.add_invoice(invoice("AS-0", id="inv_0"))
    repo.add_transaction(t)
    repo.add_invoice(invoice("AS-0", transaction_id="t1", id="inv_0"))  # t1 documented by AS-0
    repo.add_invoice(invoice("AS-1", id="inv_1"))  # unlinked invoice of the same amount, number matches t1's reference
    again = load.ingest_transaction(repo, t)
    assert again.documented is True and again.invoice_id == "inv_0"
    assert repo.get_invoice_by_number("AS-1").transaction_id is None


def test_attach_takes_the_invoice_lock_before_the_payment_row(factory):
    """Lock order is invoice number first, payment row second (same as add_invoice), so no deadlock."""
    with factory() as r:
        r.add_transaction(txn("t1"))
    url = get_settings().database_url
    schema_url = url + ("&" if "?" in url else "?") + "options=" + __import__("urllib.parse").parse.quote(f"-csearch_path={SCHEMA}")
    blocker = psycopg.connect(schema_url)
    blocker.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("invoice:AS-1",))  # add_invoice mid-flight
    res = {}
    th = threading.Thread(target=lambda: res.update(CompanyDbTools(factory).attach_invoice(
        "t1", "AS-1", "Asimex", "2025-12-01", "100.00")))
    th.start()
    try:
        th.join(0.7)
        assert th.is_alive()  # attach waits for the invoice lock...
        probe = psycopg.connect(schema_url)
        try:  # ...while NOT holding the payment row
            probe.execute("SELECT id FROM transactions WHERE id = 't1' FOR UPDATE NOWAIT")
        finally:
            probe.rollback()
            probe.close()
    finally:
        blocker.rollback()
        blocker.close()
    th.join(timeout=15)
    assert res.get("ok") is True


def test_concurrent_relink_and_attach_do_not_deadlock(factory):
    with factory() as r:
        r.add_transaction(txn("t1"))
        r.add_invoice(invoice("AS-1", id="inv_1"))
    tools = CompanyDbTools(factory)

    def relink():
        try:
            with factory() as r:
                r.add_invoice(invoice("AS-1", transaction_id="t1", id="inv_1"))
            return "ok"
        except psycopg.errors.DeadlockDetected:
            return "deadlock"

    for _ in range(5):
        with factory() as r:
            r.conn.execute("UPDATE transactions SET documented = FALSE, invoice_id = NULL")
            r.conn.execute("UPDATE invoices SET transaction_id = NULL")
            r.conn.commit()
        res = _race([relink, lambda: tools.attach_invoice("t1", "AS-1", "Asimex", "2025-12-01", "100.00")])
        assert res[0] == "ok", res
