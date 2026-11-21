"""Runtime Gmail authorization remains label-scoped and revoked grants stop appearing connected."""
from __future__ import annotations

import base64
import types

import pytest
from google.auth import exceptions as google_exceptions

from onboarding_helpers import tenant_rows
from test_registry import FakeGmail, FakeTokenRequest, NOW, _Exec, build, store  # re-use the real registry fixture


def test_missing_label_marks_only_the_saved_mail_connection_errored(build, db_url, org):
    from scout.connectors.mail import MailLabelMissing

    store(db_url, org, "email", {"email": "boss@acme.example", "label": "Deleted"}, "rt-saved")
    mailbox = build()[0].mail
    mailbox._svc = FakeGmail([{"id": "INBOX", "name": "INBOX"}])
    with pytest.raises(MailLabelMissing):
        mailbox.search(["invoice"], NOW)
    [row] = tenant_rows(db_url, org, "SELECT status,last_error FROM connections WHERE id='gmail'")
    assert row["status"] == "error"
    assert row["last_error"] and "rt-saved" not in row["last_error"]
    assert build()[0].mail.name == "none"


def test_label_removed_after_an_earlier_read_is_not_cached_as_connected(build, db_url, org):
    from scout.connectors.mail import MailLabelMissing

    store(db_url, org, "email", {"email": "boss@acme.example", "label": "Invoices"}, "rt-saved")
    mailbox = build()[0].mail
    service = FakeGmail([{"id": "Label_invoices", "name": "Invoices"}])
    mailbox._svc = service
    assert mailbox.search(["invoice"], NOW) == []
    service.label_list = []
    with pytest.raises(MailLabelMissing):
        mailbox.search(["invoice"], NOW)
    [row] = tenant_rows(db_url, org, "SELECT status FROM connections WHERE id='gmail'")
    assert row["status"] == "error"


def test_a_revoked_refresh_grant_marks_the_tenant_mail_connection_errored(build, db_url, org):
    store(db_url, org, "email", {"email": "boss@acme.example", "label": "INBOX"}, "rt-saved")
    mailbox = build()[0].mail
    with pytest.raises(google_exceptions.RefreshError):
        mailbox.credentials.refresh(FakeTokenRequest(400, {"error": "invalid_grant", "error_description": "rt-saved"}))
    [row] = tenant_rows(db_url, org, "SELECT status,last_error FROM connections WHERE id='gmail'")
    assert row["status"] == "error"
    assert "rt-saved" not in row["last_error"]


class ScopedMessages(FakeGmail):
    def __init__(self, labels):
        super().__init__([{"id": "Label_invoices", "name": "Invoices"}])
        self.message_labels = labels
        self.calls = []

    def messages(self):
        def get(**kwargs):
            self.calls.append(kwargs["format"])
            if kwargs["format"] == "minimal":
                return _Exec({"id": "message", "labelIds": self.message_labels})
            assert "Label_invoices" in self.message_labels, "Fetched an out-of-scope message body"
            return _Exec({"id": "message", "labelIds": self.message_labels, "payload": {
                "parts": [{"filename": "invoice.pdf", "body": {"attachmentId": "part"}}]}})

        return types.SimpleNamespace(get=get, attachments=lambda: types.SimpleNamespace(
            get=lambda **kwargs: _Exec({"data": base64.urlsafe_b64encode(b"allowed document").decode()})))


def test_attachment_rejects_out_of_label_ids_before_fetching_private_bodies():
    from scout.connectors.mail import GmailMail

    service = ScopedMessages(["INBOX"])
    mailbox = GmailMail(None, "Invoices", service=service, require_label=True)
    with pytest.raises(PermissionError):
        mailbox.attachment("message", "invoice.pdf")
    assert service.calls == ["minimal"]


def test_attachment_in_the_authorized_label_is_readable():
    from scout.connectors.mail import GmailMail

    service = ScopedMessages(["Label_invoices"])
    mailbox = GmailMail(None, "Invoices", service=service, require_label=True)
    assert mailbox.attachment("message", "invoice.pdf") == b"allowed document"
    assert service.calls == ["minimal", "full"]
