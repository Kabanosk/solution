from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from cfo.db.repo import Repository
from cfo.domain import Transaction
from scout.audit import Audit
from scout.connectors.bank import RevolutMockBank
from scout.connectors.chat import SlackChat, amounts_in, matches
from scout.connectors.crypto import new_key, open_, seal
from scout.connectors.files import WorkstationFiles
from scout.connectors.invoicing import PostgresInvoicing
from scout.connectors.mail import FolderMail, GmailMail, UnavailableMail
from scout.models import InvoiceDoc
from scout.seed import plant_demo_evidence

NOW = datetime.now(timezone.utc)


def test_maps_revolut_shape():
    item = {"id": "tx_1", "type": "card_payment", "state": "completed", "created_at": NOW.isoformat(),
            "completed_at": NOW.isoformat(), "reference": "Team lunch", "merchant": {"name": "Lunch Lab"},
            "legs": [{"amount": -1250.0, "currency": "EUR", "description": "Lunch Lab"}],
            "card": {"card_number": "****4417", "employee_id": "vanessa"}}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[item]))
    bank = RevolutMockBank("http://bank", http=httpx.Client(base_url="http://bank", transport=transport))
    [t] = bank.list_transactions(NOW - timedelta(minutes=5))
    assert t.amount == Decimal("-1250.0") and t.counterparty == "Lunch Lab"
    assert t.card_last4 == "4417" and t.holder_employee_id == "vanessa" and t.outgoing


def _book(conn_factory, txn_id="txn_1", amount="-100.00"):
    conn = conn_factory()
    Repository(conn).add_transaction(Transaction(id=txn_id, booking_date=date.today(), type="SEPA", reference="r",
                                                 counterparty="Asimex Wholesale Ltd", amount=Decimal(amount)))
    conn.close()


def _doc(number="AS-1", total="100.00"):
    return InvoiceDoc(number=number, supplier="Asimex Wholesale Ltd", issue_date=date.today(), total=Decimal(total),
                      source="files:Downloads/x.pdf")


def test_invoicing_file_and_find(conn_factory):
    inv = PostgresInvoicing(conn_factory, Audit(conn_factory))
    _book(conn_factory)
    assert inv.file(_doc(), "txn_1") == (True, "inv_as1")
    [found] = inv.find(number="as-1")
    assert found.transaction_id == "txn_1"
    assert inv.find(supplier="Asimex Wholesale Ltd", amount=Decimal("-100.00"), around=date.today())


def test_file_refuses_mismatch(conn_factory):
    inv = PostgresInvoicing(conn_factory, Audit(conn_factory))
    _book(conn_factory)
    assert inv.file(_doc(total="99.00"), "txn_1") == (False, "amount_mismatch")


def test_file_refuses_linked_elsewhere(conn_factory):
    inv = PostgresInvoicing(conn_factory, Audit(conn_factory))
    _book(conn_factory, "txn_1")
    _book(conn_factory, "txn_2")
    assert inv.file(_doc(), "txn_1")[0]
    assert inv.file(_doc(), "txn_2") == (False, "linked_elsewhere")
    assert inv.file(_doc(), "txn_1") == (False, "already_documented")


def test_private_folder_denied_and_audited(conn_factory, db, tmp_path):
    plant_demo_evidence(tmp_path, date.today())
    files = WorkstationFiles(tmp_path / "workstations", Audit(conn_factory))
    assert files.read("grazyna", "Private/personal-notes.txt", task_id="SC-1") is None
    found = files.search("grazyna", ["AS-77012"], task_id="SC-1")
    assert [f["name"] for f in found] == ["Invoice_AS-77012.pdf"]
    assert files.read("grazyna", found[0]["path"]).startswith(b"%PDF")
    rows = db.execute("SELECT decision, target FROM audit_log ORDER BY id").fetchall()
    assert rows[0]["decision"] == "DENY" and rows[0]["target"] == "files:grazyna"
    assert any(r["decision"] == "ALLOW" for r in rows)


def test_folder_mail_search_finds_pdf_attachment(tmp_path):
    plant_demo_evidence(tmp_path, date.today())
    mail = FolderMail(tmp_path / "mailbox")
    [hit] = mail.search(["CWM-4471"], NOW - timedelta(days=30))
    assert hit.attachments == ["CWM-4471.pdf"]
    assert mail.attachment(hit.id, "CWM-4471.pdf").startswith(b"%PDF")
    new, cur = mail.changes({})
    assert len(new) == 2 and mail.changes(cur)[0] == []  # CWM-4471 and the poisoned AS-77666


def test_mail_unavailable_is_a_step_not_a_crash():
    m = UnavailableMail()
    assert m.available() is False and m.search(["x"], NOW) == [] and m.attachment("a", "b") is None


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGmail:
    def __init__(self):
        self.list_kwargs = None

    def users(self):
        return self

    def labels(self):
        return self

    def messages(self):
        return self

    def list(self, **kw):
        if "q" in kw:
            self.list_kwargs = kw
            return _Exec({"messages": [{"id": "m1"}]})
        return _Exec({"labels": [{"id": "Label_7", "name": "Amalfi"}]})

    def get(self, **kw):
        return _Exec({"id": "m1", "internalDate": str(int(NOW.timestamp() * 1000)), "snippet": "invoice",
                      "payload": {"headers": [{"name": "Subject", "value": "Invoice AS-77001"},
                                              {"name": "From", "value": "billing@asimex.example"}],
                                  "parts": [{"filename": "AS-77001.pdf", "body": {"attachmentId": "a1"}}]}})


def test_gmail_query_and_label():
    svc = FakeGmail()
    mail = GmailMail(None, "Amalfi", service=svc)
    [hit] = mail.search(["AS-77001", "Asimex"], NOW - timedelta(days=30))
    assert '"AS-77001"' in svc.list_kwargs["q"] and "after:" in svc.list_kwargs["q"]
    assert svc.list_kwargs["labelIds"] == ["Label_7"]
    assert hit.subject == "Invoice AS-77001" and hit.attachments == ["AS-77001.pdf"]
    assert GmailMail(None, None, service=FakeGmail()).query([], NOW).startswith("after:")  # no words: newest mails


@pytest.mark.parametrize("text", ["refund of 1,250.00 EUR", "refund 1.250,00 €", "1250,00", "€1250", "1250 EUR"])
def test_amount_formats(text):
    assert Decimal("1250") in amounts_in(text)


def test_match_rules():
    assert matches("about FLV-42026 again", invoice_number="FLV-42026", supplier=None, amount=None)
    assert not matches("Fattoria delivered late", invoice_number="FLV-1", supplier="Fattoria La Vino GmbH",
                       amount=Decimal("-1840.00"))
    assert matches("Pasta Fresca will refund the 1,250.00 deposit", invoice_number="PFB-9",
                   supplier="Pasta Fresca Bologna S.r.l.", amount=Decimal("-1250.00"))


def test_slack_chat_search(fake_slack, conn_factory, db):
    fake_slack.history["C_FIN"] = [{"ts": "1.1", "user": "U1", "text": "Pasta Fresca cancelled, refund 1,250.00"},
                                   {"ts": "1.2", "user": "U2", "text": "lunch?"}]
    chat = SlackChat(fake_slack, ["#finance"], Audit(conn_factory))
    [hit] = chat.search(since=NOW - timedelta(days=30), invoice_number="PFB-2611",
                        supplier="Pasta Fresca Bologna S.r.l.", amount=Decimal("-1250.00"), task_id="SC-1")
    assert hit.channel == "finance" and hit.permalink.endswith("/C_FIN/1.1")
    assert db.execute("SELECT target FROM audit_log").fetchone()["target"] == "chat:#finance"


def test_crypto_roundtrip():
    key = new_key()
    sealed = seal("refresh-token", key)
    assert b"refresh-token" not in sealed and open_(sealed, key) == "refresh-token"
