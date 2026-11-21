"""Build the enabled connectors from the `connections` table (one company per deployment)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scout.audit import Audit
from scout.config import REPO_ROOT, ScoutSettings
from scout.connectors.bank import BankConnector, RevolutMockBank
from scout.connectors.chat import ChatConnector, NoChat, SlackChat
from scout.connectors.crypto import derive_key, open_, seal
from scout.connectors.files import FilesConnector, WorkstationFiles
from scout.connectors.invoicing import InvoicingConnector, PostgresInvoicing
from scout.connectors.ksef import KsefLookup
from scout.connectors.mail import (
    FolderMail,
    GmailMail,
    MailConnector,
    UnavailableMail,
    WorkspaceMail,
)
from scout.db import ConnFactory

log = logging.getLogger(__name__)


@dataclass
class Connectors:
    bank: BankConnector
    invoicing: InvoicingConnector
    files: FilesConnector
    mail: MailConnector
    chat: ChatConnector
    ksef: KsefLookup | None = None  # set when the KSeF connection is connected (it has a NIP)


class UnavailableBank:
    name = "not_configured"
    label = "Bank not configured"

    def list_transactions(self, since):
        return []


class UnavailableFiles:
    name = "not_configured"

    def search(self, owner, terms, *, task_id=None):
        return []

    def read(self, owner, path, *, task_id=None):
        return None


def _path(p: str) -> Path:
    return Path(p) if Path(p).is_absolute() else REPO_ROOT / p


def _rows(conn_factory: ConnFactory) -> dict[str, dict[str, Any]]:
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT c.*, cc.ciphertext FROM connections c LEFT JOIN connection_credentials cc ON cc.connection_id = c.id "
            "WHERE c.enabled"
        ).fetchall()
    return {r["id"]: r for r in rows}


def build_mail(rows: dict[str, dict[str, Any]], settings: ScoutSettings, audit: Audit) -> MailConnector:
    gmail = rows.get("gmail")
    if gmail and gmail["status"] == "connected" and gmail.get("ciphertext") and settings.connections_key:
        try:
            secret = open_(gmail["ciphertext"], settings.connections_key)
            cfg = gmail["config"] or {}
            if gmail["provider"] == "google_workspace":
                return WorkspaceMail(json.loads(secret), cfg.get("mailboxes", []), cfg.get("label"), audit)
            return GmailMail.from_refresh_token(secret, settings.google_client_id, settings.google_client_secret,
                                                cfg.get("label") or settings.google_mail_label, audit,
                                                mailbox=cfg.get("email", "me"))
        except Exception:  # noqa: BLE001 - a broken credential must not stop the loop
            log.exception("gmail connection unusable, falling back")
    folder = rows.get("mail_folder")
    if folder:
        return FolderMail(_path((folder["config"] or {}).get("root", "data/generated/mailbox")), audit)
    return UnavailableMail()


def build(conn_factory: ConnFactory, settings: ScoutSettings, slack_client: Any = None,
          audit: Audit | None = None) -> Connectors:
    from cfo.tenancy import current_tenant, legacy_connectors_enabled
    audit = audit or Audit(conn_factory)
    if not legacy_connectors_enabled():
        return build_tenant(conn_factory, settings, audit, current_tenant(required=False))
    rows = _rows(conn_factory)
    chat: ChatConnector = NoChat()
    if slack_client is not None and "slack" in rows:
        chat = SlackChat(slack_client, (rows["slack"]["config"] or {}).get("channels") or settings.chat_channels, audit)
    return Connectors(
        bank=RevolutMockBank(settings.bank_api_url),
        invoicing=PostgresInvoicing(conn_factory, audit),
        files=WorkstationFiles(_path(settings.workstations_root), audit),
        mail=build_mail(rows, settings, audit),
        chat=chat,
        ksef=build_ksef(rows, settings, audit),
    )


def _authorized_rows(conn_factory: ConnFactory) -> dict[str, dict[str, Any]]:
    """Only rows the owner explicitly connected through setup (`managed_by`), currently connected and enabled."""
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT c.*, cc.ciphertext FROM connections c LEFT JOIN connection_credentials cc ON cc.connection_id = c.id "
            "WHERE c.enabled AND c.status = 'connected' AND c.config->>'managed_by' = 'onboarding'").fetchall()
    return {r["id"]: r for r in rows}


def build_tenant(conn_factory: ConnFactory, settings: ScoutSettings, audit: Audit, tenant: Any) -> Connectors:
    """Connectors of a non-legacy organization: unavailable unless the owner authorized that exact connector through
    setup. There is no global, folder, workstation or environment-token fallback of any kind. Every connector is built
    independently: one broken credential leaves only that connector unavailable."""
    closed = Connectors(bank=UnavailableBank(), invoicing=PostgresInvoicing(conn_factory, audit),
                        files=UnavailableFiles(), mail=UnavailableMail(), chat=NoChat())
    if tenant is None:
        return closed
    from scout.onboarding.catalog import CONNECTION
    try:
        rows = _authorized_rows(conn_factory)
    except Exception:  # noqa: BLE001 - e.g. a schema without connections: stay closed
        log.exception("could not read tenant connections")
        return closed

    def usable(step_id: str) -> tuple[dict[str, Any], str | None] | None:
        cid, _, provider = CONNECTION[step_id]
        row = rows.get(cid)
        if not row or row["provider"] != provider:
            return None
        if row.get("ciphertext") is None:
            return row, None
        try:
            return row, open_(row["ciphertext"], derive_key(settings.connections_key, tenant.id, cid))
        except Exception:  # noqa: BLE001 - foreign or corrupt ciphertext is simply unusable
            log.warning("tenant %s credential for %s cannot be opened", tenant.id, cid)
            return None

    if (found := usable("bank")) and found[1]:
        bank_cipher = found[0]["ciphertext"]

        def on_bank_refresh(blob: dict[str, Any]) -> None:
            nonlocal bank_cipher
            renewed = seal(json.dumps(blob), derive_key(settings.connections_key, tenant.id, "bank"))
            with conn_factory() as conn:
                changed = conn.execute(
                    "UPDATE connection_credentials cc SET ciphertext=%s FROM connections c "
                    "WHERE cc.connection_id='bank' AND cc.ciphertext=%s AND c.id=cc.connection_id "
                    "AND c.status='connected' AND c.config->>'managed_by'='onboarding' RETURNING cc.connection_id",
                    (renewed, bank_cipher)).fetchone()
            if changed:
                bank_cipher = renewed

        try:
            from scout.connectors.bunq import BunqSandboxBank
            closed.bank = BunqSandboxBank(json.loads(found[1]), on_refresh=on_bank_refresh)
        except Exception:  # noqa: BLE001
            log.warning("bunq sandbox connection unusable")
    if (found := usable("email")) and found[1]:
        cfg = found[0]["config"] or {}
        mail_cipher = found[0]["ciphertext"]

        def on_mail_error() -> None:
            # Compare the original credential so an old in-flight read cannot disable a newly authorized mailbox.
            with conn_factory() as conn:
                changed = conn.execute(
                    "UPDATE connections SET status='error', last_error=%s WHERE id='gmail' AND status='connected' "
                    "AND config->>'managed_by'='onboarding' AND EXISTS "
                    "(SELECT 1 FROM connection_credentials cc WHERE cc.connection_id='gmail' AND cc.ciphertext=%s) RETURNING id",
                    ("Google mailbox authorization or the selected label is no longer available. Reconnect this mailbox.", mail_cipher)).fetchone()
                if changed:
                    from scout.onboarding.state import record
                    record(conn, tenant.id, "email")

        try:
            closed.mail = GmailMail.from_refresh_token(found[1], settings.google_client_id,
                                                       settings.google_client_secret, cfg.get("label") or "INBOX",
                                                       audit, mailbox=cfg.get("email") or "me", require_label=True,
                                                       on_error=on_mail_error)
        except Exception:  # noqa: BLE001
            log.warning("tenant Gmail connection unusable")
    if (found := usable("slack")) and found[1]:
        channels = (found[0]["config"] or {}).get("channels") or []
        if channels:
            try:
                from slack_sdk import WebClient
                closed.chat = SlackChat(WebClient(token=found[1], timeout=10), channels, audit)
            except Exception:  # noqa: BLE001
                log.exception("slack connection unusable")
    if found := usable("ksef"):
        cfg = found[0]["config"] or {}
        if cfg.get("nip"):  # test environment only, no known demo sellers
            from scout.connectors.ksef import TEST_URL
            closed.ksef = KsefLookup(cfg["nip"], "Scout test company", audit, base_url=TEST_URL, sellers=set())
    return closed


def build_ksef(rows: dict[str, dict[str, Any]], settings: ScoutSettings, audit: Audit) -> KsefLookup | None:
    row = rows.get("ksef")
    cfg = (row or {}).get("config") or {}
    if not row or row["status"] != "connected" or not cfg.get("nip"):
        return None
    from scout.ksef_sync import known_sellers

    return KsefLookup(cfg["nip"], cfg.get("company") or "Pizzeria Amalfi", audit, base_url=settings.ksef_api_url,
                      sellers=known_sellers(cfg["nip"]))
