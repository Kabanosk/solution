"""Cycle 7: the connector registry builds only explicitly authorized, tenant-owned connectors; no global fallback."""
from __future__ import annotations

import json
import types
from datetime import datetime, timezone

import pytest
from google.auth import exceptions as google_exceptions
from google.auth.transport import Request

from onboarding_helpers import MASTER_KEY, admin
from bunq_fixtures import API_KEY, USER_ID

NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status, self.headers, self.data = status, {}, json.dumps(body).encode()


class FakeTokenRequest(Request):
    """google-auth transport double: the real refresh logic runs against scripted token-endpoint HTTP responses."""

    def __init__(self, status: int, body: dict):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        self.calls.append((url, method, (body or b"").decode() if isinstance(body, bytes) else body))
        return FakeResponse(self.status, self.body)


def store(db_url, org, step_id, config, secret=None, *, status="connected", enabled=True, managed=True, key_org=None,
          provider=None):
    """Write a connection exactly like the onboarding service does (same helpers), plus deliberate variations."""
    from cfo.tenancy import Tenant, bind_tenant
    from scout import db as scout_db
    from scout.config import ScoutSettings
    from scout.onboarding import service
    from scout.onboarding.catalog import CONNECTION
    settings = ScoutSettings(connections_key=MASTER_KEY)
    cid, kind, prov = CONNECTION[step_id]
    cipher = service.seal_secret(settings, key_org or org.id, step_id, secret) if secret is not None else None
    with bind_tenant(Tenant(id=org.id, slug=org.slug, schema_name=org.schema, employee_id=org.id, role="owner")):
        conn = scout_db.connect(db_url)
    service.upsert_connection(conn, step_id, "x", {**config, **({"managed_by": "onboarding"} if managed else {})}, cipher)
    conn.execute("UPDATE connections SET status=%s, enabled=%s, provider=%s, config=%s WHERE id=%s",
                 (status, enabled, provider or prov,
                  __import__("psycopg").types.json.Jsonb({**config, **({"managed_by": "onboarding"} if managed else {})}), cid))
    conn.commit()
    conn.close()


@pytest.fixture
def build(db_url, org, settings_factory):
    from cfo.tenancy import Tenant, bind_tenant
    from scout import db as scout_db
    from scout.audit import Audit
    from scout.config import tenant_settings
    from scout.connectors import registry

    def run(slack_client=None, **overrides):
        raw = settings_factory(database_url=db_url, slack_bot_token="xoxb-GLOBAL", slack_user_map="a=B",
                               workstations_root="/srv/global", chat_channels=["finance"], google_mail_label="Amalfi",
                               **overrides)
        tenant = Tenant(id=org.id, slug=org.slug, schema_name=org.schema, employee_id=org.id, role="owner")
        with bind_tenant(tenant):
            shaped = tenant_settings(raw)
            cf = scout_db.factory(db_url)
            return registry.build(cf, shaped, slack_client, Audit(cf)), shaped

    return run


def test_unconfigured_tenant_gets_only_unavailable_connectors_and_never_the_global_ones(build, db_url, org):
    # a stray local-folder mail row must not become a fallback for a tenant
    admin(db_url).execute(f'INSERT INTO "{org.schema}".connections(id,kind,provider,display_name,config) '
                          "VALUES ('mail_folder','mail','eml_folder','Mail archive','{\"root\":\"/tmp\"}')")
    connectors, _ = build(slack_client=object())
    assert connectors.bank.name == "not_configured" and connectors.files.name == "not_configured"
    assert connectors.mail.name == "none" and connectors.mail.available() is False
    assert connectors.chat.name == "none" and connectors.ksef is None
    assert connectors.invoicing.name == "invoicing_pg"


def test_unmanaged_disabled_errored_or_foreign_rows_are_ignored(build, db_url, org, other_org):
    store(db_url, org, "email", {"email": "a@b.test", "label": "INBOX"}, "refresh-A", managed=False)
    assert build()[0].mail.name == "none"
    store(db_url, org, "email", {"email": "a@b.test", "label": "INBOX"}, "refresh-A", enabled=False)
    assert build()[0].mail.name == "none"
    store(db_url, org, "email", {"email": "a@b.test", "label": "INBOX"}, "refresh-A", status="error")
    assert build()[0].mail.name == "none"
    store(db_url, org, "email", {"email": "a@b.test", "label": "INBOX"}, "refresh-A", provider="eml_folder")
    assert build()[0].mail.name == "none"
    # ciphertext sealed for another organization cannot be opened here
    store(db_url, org, "email", {"email": "a@b.test", "label": "INBOX"}, "refresh-A", key_org=other_org.id)
    assert build()[0].mail.name == "none"


def test_gmail_uses_the_saved_refresh_token_with_the_shaped_settings_and_refreshes(build, db_url, org):
    store(db_url, org, "email", {"email": "boss@acme.example", "label": "Invoices"}, "rt-saved")
    mail, shaped = build()[0].mail, build()[1]
    assert mail.name == "gmail" and mail.available() and mail.mailbox == "boss@acme.example"
    creds = mail.credentials
    assert creds.refresh_token == "rt-saved"
    assert (creds.client_id, creds.client_secret) == ("google-client-id", "google-client-secret")
    assert list(creds.scopes) == ["https://www.googleapis.com/auth/gmail.readonly"]
    request = FakeTokenRequest(200, {"access_token": "fresh", "expires_in": 3600,
                                     "scope": "https://www.googleapis.com/auth/gmail.readonly"})
    creds.refresh(request)
    assert creds.token == "fresh"
    [(url, method, body)] = request.calls
    assert url == "https://oauth2.googleapis.com/token" and method == "POST"
    assert "grant_type=refresh_token" in body and "refresh_token=rt-saved" in body


def test_revoked_or_downscoped_grants_fail_visibly_not_silently(build, db_url, org):
    store(db_url, org, "email", {"email": "boss@acme.example", "label": "INBOX"}, "rt-saved")
    creds = build()[0].mail.credentials
    with pytest.raises(google_exceptions.RefreshError):
        creds.refresh(FakeTokenRequest(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    with pytest.raises(google_exceptions.RefreshError):
        creds.refresh(FakeTokenRequest(200, {"access_token": "x", "expires_in": 3600, "scope": "openid"}))


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGmail:
    """The slice of the Gmail API client GmailMail uses (users().labels().list / users().messages().list)."""

    def __init__(self, labels):
        self.label_list, self.listed = labels, []

    def users(self):
        return self

    def labels(self):
        return types.SimpleNamespace(list=lambda **kw: _Exec({"labels": self.label_list}))

    def messages(self):
        def list_(**kw):
            self.listed.append(kw)
            return _Exec({"messages": []})
        return types.SimpleNamespace(list=list_)


def test_a_missing_label_never_widens_the_search_to_the_whole_mailbox():
    from scout.connectors.mail import GmailMail, MailLabelMissing
    service = FakeGmail([{"id": "INBOX", "name": "INBOX"}])
    strict = GmailMail(None, "Deleted", None, service=service, require_label=True)
    with pytest.raises(MailLabelMissing):
        strict.search(["invoice"], NOW)
    assert service.listed == []
    # legacy behaviour is unchanged for the reserved legacy organization
    lenient = GmailMail(None, "Deleted", None, service=service)
    lenient.search(["invoice"], NOW)
    assert service.listed and "labelIds" not in service.listed[0]


def test_inbox_label_resolves_to_the_system_label():
    from scout.connectors.mail import GmailMail
    service = FakeGmail([{"id": "INBOX", "name": "INBOX"}, {"id": "Label_1", "name": "Invoices"}])
    mail = GmailMail(None, "INBOX", None, service=service, require_label=True)
    mail.search(["invoice"], NOW)
    assert service.listed[0]["labelIds"] == ["INBOX"]


def test_bunq_row_builds_the_read_only_sandbox_bank(build, db_url, org):
    blob = {"api_key": API_KEY, "installation_token": "i", "private_key_pem": "p", "session_token": "s",
            "session_id": 5, "user_id": USER_ID}
    store(db_url, org, "bank", {"mode": "sandbox"}, json.dumps(blob))
    bank = build()[0].bank
    assert bank.name == "bunq_sandbox" and bank.label == "bunq sandbox"


def test_ksef_row_builds_a_test_only_lookup_without_demo_suppliers(build, db_url, org):
    store(db_url, org, "ksef", {"env": "test", "nip": "5260250995", "api": "https://api.ksef.mf.gov.pl/v2"})
    ksef = build(ksef_api_url="https://api.ksef.mf.gov.pl/v2")[0].ksef
    assert ksef is not None and ksef.nip == "5260250995" and ksef.sellers == set()
    assert ksef._base_url == "https://api-test.ksef.mf.gov.pl/v2"


def test_slack_row_reads_only_explicitly_authorized_channels_with_its_own_token(build, db_url, org):
    store(db_url, org, "slack", {"team": "Acme", "team_id": "T1", "channels": []}, "xoxb-tenant")
    assert build(slack_client=object())[0].chat.name == "none"  # connected but no channel authorized yet
    store(db_url, org, "slack", {"team": "Acme", "team_id": "T1", "channels": ["finance"]}, "xoxb-tenant")
    chat = build(slack_client=object())[0].chat
    assert chat.name == "slack" and chat._client.token == "xoxb-tenant" and chat._wanted == ["finance"]


def test_a_tenant_never_sees_another_tenants_connections(build, db_url, org, other_org):
    store(db_url, other_org, "email", {"email": "other@x.test", "label": "INBOX"}, "rt-other")
    assert build()[0].mail.name == "none"
