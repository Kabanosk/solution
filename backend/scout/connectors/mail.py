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
from functools import wraps
from typing import Any, Callable, Protocol

from scout.audit import Audit
from scout.models import MailHit

log = logging.getLogger(__name__)
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def _notify_mail_error(callback: Callable[[], None] | None) -> None:
    if callback:
        try:
            callback()
        except Exception:  # noqa: BLE001 - preserve the original read failure without logging credential details
            log.warning("could not record Gmail authorization failure")


def _strict_credentials(on_error: Callable[[], None] | None = None) -> type:
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials

    class StrictCredentials(Credentials):
        def _perform_refresh_token(self, request):  # noqa: ANN001
            try:
                super()._perform_refresh_token(request)
                if not set(GMAIL_SCOPES) <= set(self.granted_scopes or []):
                    raise RefreshError("The Gmail read-only scope is no longer granted")
            except RefreshError as error:
                self.token = None
                if not getattr(error, "retryable", False):
                    _notify_mail_error(on_error)
                raise RefreshError("Gmail authorization could not be refreshed", retryable=getattr(error, "retryable", False)) from None

    return StrictCredentials


class MailLabelMissing(RuntimeError):
    """The authorized Gmail label no longer exists. Reads stop instead of falling back to the whole mailbox."""


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


def _guard_mail_read(method):  # noqa: ANN001
    @wraps(method)
    def guarded(self, *args, **kwargs):  # noqa: ANN001
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            from google.auth.exceptions import RefreshError
            from googleapiclient.errors import HttpError

            failed_grant = isinstance(error, RefreshError) and not getattr(error, "retryable", False)
            rejected = isinstance(error, HttpError) and getattr(error.resp, "status", None) in (401, 403)
            if self._require_label and (isinstance(error, MailLabelMissing) or failed_grant or rejected):
                _notify_mail_error(self._on_error)
            if self._require_label and rejected:
                raise PermissionError("Gmail read authorization is no longer available") from None
            raise
    return guarded


class GmailMail:
    """Gmail API over OAuth credentials (a user who clicked Connect Google) for one mailbox."""

    name = "gmail"

    def __init__(self, creds: Any, label: str | None, audit: Audit | None = None, mailbox: str = "me",
                 service: Any = None, require_label: bool = False, on_error: Callable[[], None] | None = None):
        if service is None:
            from googleapiclient.discovery import build
            from google_auth_httplib2 import AuthorizedHttp
            from httplib2 import Http

            service = build("gmail", "v1", http=AuthorizedHttp(creds, http=Http(timeout=10)), cache_discovery=False)
        self._svc = service
        self.credentials = creds  # kept so a refresh/revocation problem can be observed and tested
        self._audit = audit
        self.mailbox = mailbox
        self._label_name = label
        self._label_id: str | None = None
        # Tenant mailboxes: a configured label that cannot be found must stop the read, never widen it to the mailbox.
        self._require_label = require_label
        self._on_error = on_error

    @classmethod
    def from_refresh_token(cls, refresh_token: str, client_id: str, client_secret: str, label: str | None,
                           audit: Audit | None = None, mailbox: str = "me", require_label: bool = False,
                           on_error: Callable[[], None] | None = None) -> GmailMail:
        from google.oauth2.credentials import Credentials

        # Tenant mailboxes (require_label) also fail visibly if the grant lost its scope: google-auth alone only logs it.
        credentials_class = _strict_credentials(on_error) if require_label else Credentials
        creds = credentials_class(None, refresh_token=refresh_token, token_uri=TOKEN_URI, client_id=client_id,
                                  client_secret=client_secret, scopes=GMAIL_SCOPES)
        return cls(creds, label, audit, mailbox, require_label=require_label, on_error=on_error)

    def available(self) -> bool:
        return True

    def label_id(self) -> str | None:
        if self._label_name and (self._require_label or self._label_id is None):
            labels = self._svc.users().labels().list(userId="me").execute().get("labels", [])
            found = next((x["id"] for x in labels if x["name"].casefold() == self._label_name.casefold()), None)
            if found is None and self._require_label:
                raise MailLabelMissing(f"Gmail label '{self._label_name}' not found")
            self._label_id = found or ""
        return self._label_id or None

    def query(self, terms: list[str], since: datetime) -> str:
        quoted = " OR ".join(f'"{t}"' for t in terms if t)
        after = f"after:{int(since.timestamp())}"
        return f"({quoted}) {after}" if quoted else after  # no words: the newest mails

    def _assert_message_scope(self, mid: str) -> None:
        if self._require_label:
            label = self.label_id()
            metadata = self._svc.users().messages().get(userId="me", id=mid, format="minimal").execute()
            if not label or label not in metadata.get("labelIds", []):
                raise PermissionError("Message is outside the authorized Gmail label")

    def _hit(self, mid: str) -> MailHit:
        self._assert_message_scope(mid)
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

    @_guard_mail_read
    def search(self, terms, since, *, task_id=None):  # noqa: ANN001
        kwargs: dict[str, Any] = {"userId": "me", "q": self.query(terms, since), "maxResults": 10}
        if self.label_id():
            kwargs["labelIds"] = [self.label_id()]
        res = self._svc.users().messages().list(**kwargs).execute()
        if self._audit:
            self._audit.record("agent", "search", f"mail:{self.mailbox}", kwargs["q"], task_id=task_id,
                               policy=f"label:{self._label_name}" if self._label_name else None)
        return [self._hit(m["id"]) for m in res.get("messages", [])]

    @_guard_mail_read
    def pdf_search(self, words, since, *, task_id=None):  # noqa: ANN001
        """Mails with a PDF attached (Gmail `has:attachment filename:pdf`), optionally mentioning `words`."""
        quoted = " OR ".join(f'"{w}"' for w in words if w)
        q = f"has:attachment filename:pdf after:{int(since.timestamp())}" + (f" ({quoted})" if quoted else "")
        kwargs: dict[str, Any] = {"userId": "me", "q": q, "maxResults": 25}
        if self.label_id():
            kwargs["labelIds"] = [self.label_id()]
        res = self._svc.users().messages().list(**kwargs).execute()
        if self._audit:
            self._audit.record("agent", "search", f"mail:{self.mailbox}", q, task_id=task_id,
                               policy=f"label:{self._label_name}" if self._label_name else None)
        return [self._hit(m["id"]) for m in res.get("messages", [])]

    @_guard_mail_read
    def attachment(self, hit_id, name, *, task_id=None):  # noqa: ANN001
        self._assert_message_scope(hit_id)
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

    @_guard_mail_read
    def changes(self, cursor):  # noqa: ANN001
        if self._require_label:
            self.label_id()
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


INVOICE_WORDS = ("invoice", "invoices", "faktura", "fakturę", "faktury", "rechnung", "fattura", "factura", "facture",
                 "receipt", "rachunek", "paragon", "bill", "billing", "payment", "vat")


def _is_pdf(name: str) -> bool:
    return name.lower().endswith(".pdf")


def invoice_mails(mail: Any, since: datetime, words: list[str] | None = None, *,
                  task_id: str | None = None) -> list[MailHit]:
    """Mails with PDF attachments that look like invoices (subject, body or file name), newest first. With `words`
    (supplier, invoice number…) only those mentioning them. Uses Gmail's own attachment search when available."""
    words = [w for w in (words or []) if w]
    if hasattr(mail, "pdf_search"):
        hits = mail.pdf_search(words, since, task_id=task_id)
    else:
        hits = mail.search(words or list(INVOICE_WORDS[:6]), since, task_id=task_id)
    out = []
    for h in hits:
        pdfs = [a for a in h.attachments if _is_pdf(a)]
        if not pdfs:
            continue
        text = f"{h.subject} {h.snippet} {h.body} {' '.join(pdfs)}".casefold()
        if words or any(w in text for w in INVOICE_WORDS):
            out.append(h)
    return sorted(out, key=lambda h: h.date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


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

    def pdf_search(self, words, since, *, task_id=None):  # noqa: ANN001
        return [h for b in self._boxes for h in b.pdf_search(words, since, task_id=task_id)]

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
