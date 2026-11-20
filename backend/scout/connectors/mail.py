"""Company email: Gmail API (one-button OAuth), Google Workspace (domain-wide delegation), a local `.eml`
folder (tests, offline), or nothing connected. Read-only (`gmail.readonly`); `label` limits what Scout reads."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol

from scout.audit import Audit
from scout.models import MailHit

log = logging.getLogger(__name__)
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


class MailConnector(Protocol):
    name: str

    def available(self) -> bool: ...

    def search(self, terms: list[str], since: datetime, *, task_id: str | None = None) -> list[MailHit]: ...

    def attachment(self, hit_id: str, name: str, *, task_id: str | None = None) -> bytes | None: ...

    def changes(self, cursor: dict[str, Any]) -> tuple[list[MailHit], dict[str, Any]]: ...


class UnavailableMail:
    name = "none"
    label = "company email"

    def available(self) -> bool:
        return False

    def search(self, terms, since, *, task_id=None):  # noqa: ANN001
        return []

    def attachment(self, hit_id, name, *, task_id=None):  # noqa: ANN001
        return None

    def changes(self, cursor):  # noqa: ANN001
        return [], cursor


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _matches(hit: MailHit, terms: list[str]) -> bool:
    hay = " ".join([hit.subject, hit.snippet, hit.body, *hit.attachments]).casefold()
    return any(t.casefold() in hay for t in terms if t)


class FolderMail:
    """`.eml` files in a folder (the old laptop `Mail/` export). Not the product path: tests and offline dev."""

    name = "eml_folder"

    def __init__(self, root: Path | str, audit: Audit | None = None, mailbox: str = "office@pizzeria-amalfi.example"):
        self.root = Path(root)
        self._audit = audit
        self.mailbox = mailbox

    def available(self) -> bool:
        return self.root.is_dir()

    def _parse(self, path: Path) -> tuple[MailHit, Any]:
        msg = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        body = msg.get_body(preferencelist=("plain",))
        try:
            when = parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
        except (TypeError, ValueError):
            when = None
        text = body.get_content() if body is not None else ""
        hit = MailHit(id=path.name, mailbox=self.mailbox, sender=str(msg["From"] or ""),
                      subject=str(msg["Subject"] or ""), date=when, snippet=text[:160], body=text[:4000],
                      attachments=[a.get_filename() or "" for a in msg.iter_attachments()])
        return hit, msg

    def _all(self) -> list[tuple[MailHit, Any]]:
        return [self._parse(p) for p in sorted(self.root.glob("*.eml"))] if self.available() else []

    def search(self, terms, since, *, task_id=None):  # noqa: ANN001
        if self._audit:
            self._audit.record("agent", "search", f"mail:{self.mailbox}", " OR ".join(terms), task_id=task_id)
        hits = [h for h, _ in self._all() if (not terms or _matches(h, terms))
                and (h.date is None or _aware(h.date) >= _aware(since))]
        return sorted(hits, key=lambda h: _aware(h.date) if h.date else _EPOCH, reverse=True)

    def attachment(self, hit_id, name, *, task_id=None):  # noqa: ANN001
        path = self.root / hit_id
        if not path.is_file():
            return None
        _, msg = self._parse(path)
        for part in msg.iter_attachments():
            if part.get_filename() == name:
                if self._audit:
                    self._audit.record("agent", "read", f"mail:{self.mailbox}", f"{hit_id}/{name}", task_id=task_id)
                return part.get_payload(decode=True)
        return None

    def changes(self, cursor):  # noqa: ANN001
        seen = set(cursor.get("seen", []))
        new = [h for h, _ in self._all() if h.id not in seen]
        return new, {"seen": sorted(seen | {h.id for h in new})}


class GmailMail:
    """Gmail API over OAuth credentials (a user who clicked Connect Google) for one mailbox."""

    name = "gmail"

    def __init__(self, creds: Any, label: str | None, audit: Audit | None = None, mailbox: str = "me",
                 service: Any = None):
        if service is None:
            from googleapiclient.discovery import build

            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._svc = service
        self._audit = audit
        self.mailbox = mailbox
        self._label_name = label
        self._label_id: str | None = None

    @classmethod
    def from_refresh_token(cls, refresh_token: str, client_id: str, client_secret: str, label: str | None,
                           audit: Audit | None = None, mailbox: str = "me") -> GmailMail:
        from google.oauth2.credentials import Credentials

        creds = Credentials(None, refresh_token=refresh_token, token_uri=TOKEN_URI, client_id=client_id,
                            client_secret=client_secret, scopes=GMAIL_SCOPES)
        return cls(creds, label, audit, mailbox)

    def available(self) -> bool:
        return True

    def label_id(self) -> str | None:
        if self._label_name and self._label_id is None:
            labels = self._svc.users().labels().list(userId="me").execute().get("labels", [])
            self._label_id = next((x["id"] for x in labels if x["name"].casefold() == self._label_name.casefold()), "")
        return self._label_id or None

    def query(self, terms: list[str], since: datetime) -> str:
        quoted = " OR ".join(f'"{t}"' for t in terms if t)
        after = f"after:{int(since.timestamp())}"
        return f"({quoted}) {after}" if quoted else after  # no words: the newest mails

    def _hit(self, mid: str) -> MailHit:
        m = self._svc.users().messages().get(userId="me", id=mid, format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        body, names = "", []

        def walk(part: dict) -> None:
            nonlocal body
            if part.get("filename"):
                names.append(part["filename"])
            elif part.get("mimeType") == "text/plain" and part.get("body", {}).get("data") and not body:
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
            for p in part.get("parts", []) or []:
                walk(p)

        walk(m.get("payload", {}))
        when = datetime.fromtimestamp(int(m.get("internalDate", "0")) / 1000, tz=timezone.utc)
        return MailHit(id=mid, mailbox=self.mailbox, sender=headers.get("from", ""), subject=headers.get("subject", ""),
                       date=when, snippet=m.get("snippet", ""), body=body[:4000], attachments=names)

    def search(self, terms, since, *, task_id=None):  # noqa: ANN001
        kwargs: dict[str, Any] = {"userId": "me", "q": self.query(terms, since), "maxResults": 10}
        if self.label_id():
            kwargs["labelIds"] = [self.label_id()]
        res = self._svc.users().messages().list(**kwargs).execute()
        if self._audit:
            self._audit.record("agent", "search", f"mail:{self.mailbox}", kwargs["q"], task_id=task_id,
                               policy=f"label:{self._label_name}" if self._label_name else None)
        return [self._hit(m["id"]) for m in res.get("messages", [])]

    def attachment(self, hit_id, name, *, task_id=None):  # noqa: ANN001
        m = self._svc.users().messages().get(userId="me", id=hit_id, format="full").execute()
        stack = [m.get("payload", {})]
        while stack:
            part = stack.pop()
            if part.get("filename") == name and part.get("body", {}).get("attachmentId"):
                att = self._svc.users().messages().attachments().get(
                    userId="me", messageId=hit_id, id=part["body"]["attachmentId"]).execute()
                if self._audit:
                    self._audit.record("agent", "read", f"mail:{self.mailbox}", f"{hit_id}/{name}", task_id=task_id)
                return base64.urlsafe_b64decode(att["data"])
            stack += part.get("parts", []) or []
        return None

    def changes(self, cursor):  # noqa: ANN001
        if not cursor.get("history_id"):
            profile = self._svc.users().getProfile(userId="me").execute()
            return [], {"history_id": profile["historyId"]}
        kwargs: dict[str, Any] = {"userId": "me", "startHistoryId": cursor["history_id"],
                                  "historyTypes": ["messageAdded"]}
        if self.label_id():
            kwargs["labelId"] = self.label_id()
        res = self._svc.users().history().list(**kwargs).execute()
        ids = [a["message"]["id"] for h in res.get("history", []) for a in h.get("messagesAdded", [])]
        hits = [self._hit(i) for i in dict.fromkeys(ids)]
        return hits, {"history_id": res.get("historyId", cursor["history_id"])}


class WorkspaceMail:
    """Google Workspace: a service account with domain-wide delegation reads the authorized mailboxes."""

    name = "google_workspace"

    def __init__(self, service_account_info: dict, mailboxes: list[str], label: str | None, audit: Audit | None = None):
        from google.oauth2 import service_account

        base = service_account.Credentials.from_service_account_info(service_account_info, scopes=GMAIL_SCOPES)
        self._boxes = [GmailMail(base.with_subject(m), label, audit, mailbox=m) for m in mailboxes]

    def available(self) -> bool:
        return bool(self._boxes)

    def search(self, terms, since, *, task_id=None):  # noqa: ANN001
        return [h for b in self._boxes for h in b.search(terms, since, task_id=task_id)]

    def attachment(self, hit_id, name, *, task_id=None):  # noqa: ANN001
        for b in self._boxes:
            try:
                data = b.attachment(hit_id, name, task_id=task_id)
            except Exception:  # noqa: BLE001 - the id belongs to another mailbox
                continue
            if data:
                return data
        return None

    def changes(self, cursor):  # noqa: ANN001
        hits, out = [], {}
        for b in self._boxes:
            h, c = b.changes(cursor.get(b.mailbox, {}))
            hits += h
            out[b.mailbox] = c
        return hits, out
