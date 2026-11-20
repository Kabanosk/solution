"""KSeF: FA(3) build/parse, XAdES auth document, the one-click connect run, the API, and the KSeF rung of the
invoicing loop. A fake client stands in for KSeF; `KSEF_LIVE=1` adds one round trip against the real test API."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from typing import Any, ClassVar

import pytest
from cryptography import x509
from fastapi.testclient import TestClient
from lxml import etree

from cfo.db.repo import Repository
from cfo.domain import Invoice, InvoiceLine, Transaction
from scout import ksef_sync
from scout.audit import Audit
from scout.connectors.ksef import (
    KsefClient,
    KsefLookup,
    Line,
    Party,
    fa3_xml,
    lines_for_gross,
    parse_fa3,
    random_nip,
    seal_certificate,
    sign_auth_request,
)
from scout.demo_data import SCENARIOS
from tests.scout.test_invoicing_loop import kinds, make_world, pay, run_txn, texts

SELLER = Party("2443931649", "Asimex Wholesale Ltd", "Hafenstrasse 41", "60314 Frankfurt am Main")
BUYER = Party("6534979776", "Pizzeria Amalfi Sp. z o.o.", "ul. Neapolitańska 7", "00-001 Warszawa")


def _nip_ok(nip: str) -> bool:
    w = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    return len(nip) == 10 and sum(int(a) * b for a, b in zip(nip, w)) % 11 == int(nip[9])


def test_random_nip_has_valid_check_digit():
    assert all(_nip_ok(random_nip()) for _ in range(200))


def test_fa3_round_trip_keeps_number_parties_totals_and_due_date():
    lines = [Line("Mozzarella di bufala, 1 kg", Decimal(10), Decimal("11.80"), 8),
             Line("Pizza boxes 32 cm, 100 pcs", Decimal(2), Decimal("17.50"), 23)]
    xml = fa3_xml("AS-12345", dt.date(2025, 3, 10), SELLER, BUYER, lines, due_date=dt.date(2025, 3, 24))
    doc = parse_fa3(xml)
    # 118.00 net @8% = 127.44 gross; 35.00 net @23% = 43.05 gross
    assert doc["number"] == "AS-12345" and doc["seller_nip"] == SELLER.nip and doc["buyer_nip"] == BUYER.nip
    assert doc["supplier"] == "Asimex Wholesale Ltd" and doc["currency"] == "EUR"
    assert doc["total"] == Decimal("170.49") == sum(ln["amount"] for ln in doc["lines"])
    assert doc["issue_date"] == dt.date(2025, 3, 10) and doc["due_date"] == dt.date(2025, 3, 24)
    root = etree.fromstring(xml)
    ns = {"f": "http://crd.gov.pl/wzor/2025/06/25/13775/"}
    assert root.find("f:Fa/f:P_14_1W", ns) is not None  # VAT in PLN, required for a foreign currency


@pytest.mark.parametrize("gross", ["0.01", "1.00", "623.48", "869.62", "1286.40", "15999.99"])
@pytest.mark.parametrize("rate", [23, 8, 5])
def test_lines_for_gross_sum_to_the_payment(gross, rate):
    xml = fa3_xml("X-1", dt.date(2025, 1, 1), SELLER, BUYER, lines_for_gross(Decimal(gross), "Goods", rate))
    assert parse_fa3(xml)["total"] == Decimal(gross)


def test_auth_request_is_signed_xades_with_the_seal():
    key, cert = seal_certificate("6534979776", "Pizzeria Amalfi")
    assert cert.subject.get_attributes_for_oid(x509.ObjectIdentifier("2.5.4.97"))[0].value == "VATPL-6534979776"
    signed = etree.fromstring(sign_auth_request("20261121-CR-ABC", "6534979776", key, cert))
    ns = {"ds": "http://www.w3.org/2000/09/xmldsig#", "xades": "http://uri.etsi.org/01903/v1.3.2#"}
    assert signed.find(".//ds:Signature", ns) is not None
    assert signed.find(".//xades:SignedProperties", ns) is not None
    assert signed.find("{http://ksef.mf.gov.pl/auth/token/2.0}Challenge").text == "20261121-CR-ABC"


# -- fake KSeF ---------------------------------------------------------------------------------------------------
class FakeKsef:
    """In-memory KSeF: login per NIP, send stores invoices for the buyer NIP, received/download read them back."""

    store: ClassVar[list[dict[str, Any]]] = []
    logins: ClassVar[list[str]] = []

    def __init__(self, base_url: str = "fake") -> None:
        self.base_url = base_url

    @classmethod
    def reset(cls) -> None:
        cls.store, cls.logins = [], []

    def login(self, nip: str, name: str) -> str:
        FakeKsef.logins.append(nip)
        return f"tok:{nip}"

    def send(self, token: str, invoices: list[bytes]) -> list[dict[str, Any]]:
        out = []
        for xml in invoices:
            doc = parse_fa3(xml)
            assert token == f"tok:{doc['seller_nip']}"  # a supplier can only issue its own invoices
            kn = f"{doc['seller_nip']}-20261121-{len(FakeKsef.store):012d}-00"
            FakeKsef.store.append({"ksefNumber": kn, "invoiceNumber": doc["number"], "buyer": doc["buyer_nip"],
                                   "xml": xml})
            out.append({"ok": True, "ksefNumber": kn})
        return out

    def received(self, token: str, since: dt.datetime, *, invoice_number: str | None = None) -> list[dict[str, Any]]:
        nip = token.split(":", 1)[1]
        return [{"ksefNumber": r["ksefNumber"], "invoiceNumber": r["invoiceNumber"]} for r in FakeKsef.store
                if r["buyer"] == nip and invoice_number in (None, r["invoiceNumber"])]

    def download(self, token: str, ksef_number: str) -> bytes:
        return next(r["xml"] for r in FakeKsef.store if r["ksefNumber"] == ksef_number)


@pytest.fixture
def company(conn_factory):
    """One payment without its invoice (AS-12345) and one filed invoice (FLV-22166)."""
    FakeKsef.reset()
    conn = conn_factory()
    repo = Repository(conn)
    with repo.transaction():
        repo.add_transaction(Transaction(id="t_open", booking_date=dt.date(2025, 3, 10), type="transfer",
                                         reference="Invoice AS-12345", counterparty="Asimex Wholesale Ltd",
                                         amount=Decimal("-623.48"), invoice_number="AS-12345"))
        repo.add_transaction(Transaction(id="t_filed", booking_date=dt.date(2025, 3, 12), type="transfer",
                                         reference="Invoice FLV-22166", counterparty="Fattoria La Vino GmbH",
                                         amount=Decimal("-312.00"), invoice_number="FLV-22166"))
        repo.add_invoice(Invoice(id="inv_flv22166", number="FLV-22166", supplier="Fattoria La Vino GmbH",
                                 issue_date=dt.date(2025, 3, 11), total=Decimal("312.00"), transaction_id="t_filed",
                                 lines=[InvoiceLine(description="Prosecco", quantity=1, unit_price=Decimal(312),
                                                    amount=Decimal("312.00"))]), commit=False)
    conn.close()
    return conn_factory


def _row(cf) -> dict[str, Any]:
    with cf() as conn:
        return conn.execute("SELECT * FROM connections WHERE id = 'ksef'").fetchone()


def test_connect_issues_pulls_and_matches(company):
    with company() as conn:
        assert ksef_sync.start(conn)
        assert not ksef_sync.start(conn)  # a second click while running is refused
    res = ksef_sync.run(company, Audit(company), FakeKsef, sleep=lambda s: None)
    row = _row(company)
    cfg = row["config"]
    assert row["status"] == "connected" and row["last_sync_at"] is not None and cfg["phase"] == "done"
    assert _nip_ok(cfg["nip"]) and FakeKsef.logins[0] == cfg["nip"]
    assert res["linked"] == 1 and res["on_file"] == 1 and res["imported"] == 3  # 3 new payables
    assert cfg["linked_total"] == "623.48" and cfg["unpaid"] == 3
    assert {i["number"] for i in cfg["invoices"] if i["outcome"] == "linked"} == {"AS-12345"}
    assert all(i["ksef_number"] for i in cfg["invoices"])
    conn = company()
    repo = Repository(conn)
    assert repo.get_transaction("t_open").documented
    inv = repo.get_invoice_by_number("AS-12345")
    assert inv.source == "ksef" and inv.transaction_id == "t_open" and inv.total == Decimal("623.48")
    assert repo.get_invoice_by_number("FLV-22166").transaction_id == "t_filed"
    conn.close()
    assert any("Signed in to KSeF" in s["text"] for s in cfg["steps"])

    # a second sync issues nothing new and keeps the counts consistent
    with company() as conn:
        assert ksef_sync.start(conn)
    again = ksef_sync.run(company, Audit(company), FakeKsef, sleep=lambda s: None)
    assert again["linked"] == 0 and len(FakeKsef.store) == 5


def test_connect_failure_ends_in_error_with_reason(company):
    class Broken(FakeKsef):
        def login(self, nip: str, name: str) -> str:
            raise RuntimeError("KSeF POST /auth/xades-signature -> 400: Nieprawidłowy podpis.")

    with company() as conn:
        ksef_sync.start(conn)
    assert "error" in ksef_sync.run(company, Audit(company), Broken)
    row = _row(company)
    assert row["status"] == "error" and row["config"]["phase"] == "error"
    assert "Nieprawidłowy podpis" in row["last_error"] and row["config"]["steps"][-1]["ok"] is False


def test_api_connect_enqueues_a_job_and_refuses_a_second_run(company, monkeypatch):
    from cfo.api.app import create_app
    from scout import api as scout_api

    def conn_dep():
        conn = company()
        try:
            yield conn
        finally:
            conn.close()

    app = create_app()
    app.dependency_overrides[scout_api.get_conn] = conn_dep
    client = TestClient(app)
    r = client.post("/v1/connections/ksef:connect", json={})
    assert r.status_code == 202, r.text
    assert r.json()["connection"]["id"] == "ksef" and r.json()["job_id"]
    assert r.json()["connection"]["config"]["phase"] == "running"
    with company() as conn:
        assert conn.execute("SELECT kind FROM agent_jobs WHERE id = %s", (r.json()["job_id"],)).fetchone()["kind"] \
            == "ksef_connect"
    assert client.post("/v1/connections/ksef:connect", json={}).status_code == 409


def test_invoicing_loop_finds_the_invoice_in_ksef(conn_factory, tmp_path):
    FakeKsef.reset()
    ctx = make_world(conn_factory, tmp_path)
    nip = random_nip()
    ksef_sync.issue_demo_invoice(nip, FakeKsef)
    ctx.connectors.ksef = KsefLookup(nip, "Pizzeria Amalfi", ctx.audit, client_factory=FakeKsef, sleep=lambda s: None)
    task = run_txn(ctx, pay(ctx, "ksef"))
    assert task.status == "done" and task.level == "AUTO"
    assert kinds(ctx, task.id)[:3] == ["picked_up", "checked_system", "checked_system"]
    found = texts(ctx, task.id)[2]
    assert found.startswith("Found HMZ-20417 in KSeF as ") and "Hurtownia Mazur" in found
    assert "checked_files" not in kinds(ctx, task.id)
    conn = conn_factory()
    inv = Repository(conn).get_invoice_by_number("HMZ-20417")
    conn.close()
    assert inv.source == "ksef" and inv.total == Decimal("1509.84") and len(inv.lines) == 3


def test_invoicing_loop_without_ksef_invoice_falls_through(conn_factory, tmp_path):
    FakeKsef.reset()
    ctx = make_world(conn_factory, tmp_path)
    ctx.connectors.ksef = KsefLookup(random_nip(), "Pizzeria Amalfi", ctx.audit, client_factory=FakeKsef,
                                     sleep=lambda s: None)
    task = run_txn(ctx, pay(ctx, "files"))
    assert "Searched KSeF (national e-invoice system) for AS-77012: not issued there" in texts(ctx, task.id)
    assert task.status == "done" and task.resolved_via == "files"


@pytest.mark.skipif(os.environ.get("KSEF_LIVE") != "1", reason="network: set KSEF_LIVE=1 to call KSeF TE")
def test_live_round_trip_against_ksef_test_environment():
    nip = random_nip()
    client = KsefClient()
    number = ksef_sync.issue_demo_invoice(nip, KsefClient)
    hits = KsefLookup(nip, "Pizzeria Amalfi", None, client_factory=KsefClient).find("HMZ-20417")
    assert [h[1] for h in hits] == [number] and hits[0][0]["total"] == Decimal("1509.84")
    assert client.base_url.startswith("https://api-test.ksef.mf.gov.pl")


def test_every_cent_amount_up_to_100_eur_is_expressible():
    for cents in range(1, 10001):
        gross = Decimal(cents) / 100
        lines = lines_for_gross(gross, "Goods", 23)
        by_rate: dict[int, Decimal] = {}
        for ln in lines:
            by_rate[ln.vat_rate] = by_rate.get(ln.vat_rate, Decimal(0)) + ln.unit_net
        total = sum(n + (n * r / 100).quantize(Decimal("0.01"), rounding="ROUND_HALF_UP") for r, n in by_rate.items())
        assert total == gross, gross


def _forge(company_nip: str, number: str, total: str, supplier: str = "Evil Corp Sp. z o.o.") -> None:
    """Anyone on the shared TE can issue an invoice to our NIP under any number."""
    seller = Party(random_nip(), supplier, "ul. Ciemna 1", "00-001 Warszawa")
    buyer = Party(company_nip, "Pizzeria Amalfi Sp. z o.o.", "ul. Neapolitańska 7", "00-001 Warszawa")
    xml = fa3_xml(number, dt.date(2025, 3, 10), seller, buyer, lines_for_gross(Decimal(total), "Forged", 23))
    FakeKsef().send(f"tok:{seller.nip}", [xml])


def test_forged_invoices_never_overwrite_or_link(company):
    with company() as conn:
        ksef_sync.start(conn)
    ksef_sync.run(company, Audit(company), FakeKsef, sleep=lambda s: None)
    nip = _row(company)["config"]["nip"]
    _forge(nip, "as-12345", "0.01")      # look-alike of a filed number (old id scheme collided on inv_as12345)
    _forge(nip, "AS-12345", "99999.00")  # exact number, different document
    _forge(nip, "FLV-22166", "312.00")   # exact number and amount, unknown seller
    with company() as conn:
        ksef_sync.start(conn)
    res = ksef_sync.run(company, Audit(company), FakeKsef, sleep=lambda s: None)
    assert res["review"] >= 2
    conn = company()
    repo = Repository(conn)
    real = repo.get_invoice_by_number("AS-12345")
    assert real.total == Decimal("623.48") and real.supplier == "Asimex Wholesale Ltd" and real.transaction_id == "t_open"
    flv = repo.get_invoice_by_number("FLV-22166")
    assert flv.supplier == "Fattoria La Vino GmbH" and flv.lines[0].description == "Prosecco"
    lookalike = repo.get_invoice_by_number("as-12345")
    assert lookalike is None or (lookalike.id != real.id and lookalike.transaction_id is None)
    conn.close()


def test_untrusted_seller_is_not_filed_automatically(conn_factory, tmp_path):
    FakeKsef.reset()
    ctx = make_world(conn_factory, tmp_path)
    nip = random_nip()
    s = SCENARIOS["ksef"]
    _forge(nip, s.invoice_number, str(-s.amount))
    ctx.connectors.ksef = KsefLookup(nip, "Pizzeria Amalfi", ctx.audit, client_factory=FakeKsef, sleep=lambda s: None)
    task = run_txn(ctx, pay(ctx, "ksef"))
    assert any(t.startswith("Not filed automatically: the seller") for t in texts(ctx, task.id))
    assert task.resolved_via != "invoicing"
    conn = conn_factory()
    assert Repository(conn).get_invoice_by_number(s.invoice_number) is None
    conn.close()
