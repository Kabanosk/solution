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
from scout.connectors.crypto import open_
from scout.connectors.files import FilesConnector, WorkstationFiles
from scout.connectors.invoicing import InvoicingConnector, PostgresInvoicing
from scout.connectors.mail import FolderMail, GmailMail, MailConnector, UnavailableMail, WorkspaceMail
from scout.db import ConnFactory

log = logging.getLogger(__name__)


@dataclass
class Connectors:
    bank: BankConnector
    invoicing: InvoicingConnector
    files: FilesConnector
    mail: MailConnector
    chat: ChatConnector


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
    audit = audit or Audit(conn_factory)
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
    )
