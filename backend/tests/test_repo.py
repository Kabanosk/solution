"""Repository round-trip against the compose Postgres; skipped when it is not reachable."""

from datetime import date
from decimal import Decimal

import os
import psycopg
import pytest

from cfo.config import get_settings
from cfo.db.repo import Repository
from cfo.domain import Decision, Employee, Invoice, InvoiceLine, PolicyLevel, Rule, Team, Transaction, Vacation


@pytest.fixture
def repo():
    url = get_settings().database_url
    try:
        conn = psycopg.connect(url, connect_timeout=2)
    except psycopg.OperationalError:
        pytest.skip("Postgres not reachable")
    conn.close()
    schema = f"t001_test_{os.getpid()}"
    r = Repository.connect(url)
    r.conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    r.conn.execute(f"CREATE SCHEMA {schema}")
    r.conn.execute(f"SET search_path TO {schema}")
    r.conn.commit()
    apply_schema_in(r)
    yield r
    r.conn.rollback()
    r.conn.execute(f"DROP SCHEMA {schema} CASCADE")
    r.conn.commit()
    r.conn.close()


def apply_schema_in(r: Repository) -> None:
    from cfo.db.migrate import SCHEMA_PATH

    r.conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    r.conn.commit()


def test_transactions_invoices_employees_rules(repo: Repository):
    repo.add_team(Team(id="kitchen", name="Kitchen", monthly_budget=Decimal("9000")))
    repo.add_employee(Employee(id="vincenzo", name="Vincenzo", role="CEO"))
    repo.add_employee(Employee(id="grazyna", name="Grazyna", role="office manager", manager_id="vincenzo",
                               vacations=[Vacation(start=date(2025, 12, 20), end=date(2025, 12, 31))]))
    assert repo.get_employee("grazyna").on_vacation(date(2025, 12, 25))

    txn = Transaction(booking_date=date(2025, 12, 3), type="GIRO", reference="AS-1", counterparty="Asimex",
                      amount=Decimal("-100.00"), invoice_number="AS-1")
    repo.add_transaction(txn)
    assert [t.id for t in repo.list_transactions(undocumented_only=True)] == [txn.id]

    inv = Invoice(number="AS-1", supplier="Asimex", issue_date=date(2025, 12, 1), total=Decimal("100.00"),
                  lines=[InvoiceLine(description="Flour", unit_price=Decimal("100"), amount=Decimal("100"))],
                  transaction_id=txn.id)
    repo.add_invoice(inv)
    assert repo.get_invoice_by_number("AS-1") == inv
    assert repo.list_transactions(undocumented_only=True) == []
    assert repo.get_transaction(txn.id).documented

    dec = Decision(action_type="attach_invoice", summary="s", level=PolicyLevel.AUTO, reason="r",
                   payload={"invoice": "AS-1"})
    repo.add_decision(dec)
    assert repo.list_decisions() == [dec]

    rule = Rule(text="equipment: ask me", level=PolicyLevel.APPROVAL, category="equipment")
    repo.add_rule(rule)
    assert repo.list_rules() == [rule]


def test_job_log_merge_preserves_payload_failures_and_bounds_history(repo: Repository):
    decision = Decision(action_type="attach_invoice", summary="Attach", level=PolicyLevel.AUTO,
                        reason="Match", payload={"invoice": "AS-1", "thread_id": "evt:1"})
    other = Decision(action_type="alert", summary="Other", level=PolicyLevel.AUTO, reason="Other")
    repo.add_decision(decision)
    repo.add_decision(other)
    first = {"id": "entry-1", "ts": "2026-11-20T12:00:00Z", "level": "info", "source": "request", "message": "Recorded"}
    failure = {**first, "id": "entry-2", "level": "error", "message": "Failed"}
    success = {**first, "id": "entry-3", "message": "Completed after retry"}
    repo.merge_decision_job_log([decision.id], [first])
    repo.merge_decision_job_log([decision.id], [first, failure])
    repo.merge_decision_job_log([decision.id], [first, success])
    found = {row.id: row for row in repo.list_decisions()}
    assert found[decision.id].payload == {**decision.payload, "job_log": [first, failure, success]}
    assert found[other.id].payload == {}
    # Repeated checkpoint snapshots do not grow or reorder the persisted log.
    repo.merge_decision_job_log([decision.id], [first, success])
    assert {row.id: row for row in repo.list_decisions()}[decision.id].payload["job_log"] == [first, failure, success]
    more = [{**first, "id": f"entry-{index}"} for index in range(4, 510)]
    repo.merge_decision_job_log([decision.id], more)
    bounded = {row.id: row for row in repo.list_decisions()}[decision.id].payload
    assert len(bounded["job_log"]) == 500
    assert bounded["job_log"][-1]["id"] == "entry-509"
    assert bounded["invoice"] == "AS-1"
    # A malformed legacy log does not break recording.
    other.payload["job_log"] = "legacy"
    repo.add_decision(other)
    repo.merge_decision_job_log([other.id], [first])
    assert {row.id: row for row in repo.list_decisions()}[other.id].payload["job_log"] == [first]
