"""Chat context: messages in Slack channels the bot was added to and that are allowlisted.

A message counts only if it names the invoice number, or the supplier together with the exact amount."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from scout.audit import Audit
from scout.models import ChatHit

_NUM = re.compile(r"(?<![\w.,])(\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{2})?|\d+(?:[.,]\d{1,2})?)(?![\d])")


class ChatConnector(Protocol):
    name: str

    def search(self, *, since: datetime, invoice_number: str | None = None, supplier: str | None = None,
               amount: Decimal | None = None, task_id: str | None = None) -> list[ChatHit]: ...


def amounts_in(text: str) -> set[Decimal]:
    """Every money-looking number in `text`, reading 1,250.00 / 1.250,00 / 1250,00 / 1250 the same."""
    out: set[Decimal] = set()
    for raw in _NUM.findall(text):
        s = raw.replace(" ", "")
        if "," in s and "." in s:
            s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
        elif "," in s:
            head, _, tail = s.rpartition(",")
            s = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else s.replace(",", "")
        elif s.count(".") == 1 and len(s.rpartition(".")[2]) == 3:
            s = s.replace(".", "")
        try:
            out.add(Decimal(s))
        except InvalidOperation:
            continue
    return out


def _supplier_words(supplier: str) -> list[str]:
    stop = {"gmbh", "ltd", "s.r.l.", "srl", "ag", "the", "and", "&", "ireland", "platforms"}
    return [w for w in re.findall(r"[\w.]+", supplier.casefold()) if len(w) > 2 and w not in stop][:2]


def matches(text: str, *, invoice_number: str | None, supplier: str | None, amount: Decimal | None) -> bool:
    low = text.casefold()
    if invoice_number and invoice_number.casefold() in low:
        return True
    if supplier and amount is not None:
        words = _supplier_words(supplier)
        return bool(words) and any(w in low for w in words) and abs(Decimal(amount)) in amounts_in(text)
    return False


class SlackChat:
    name = "slack"

    def __init__(self, client: Any, channels: list[str], audit: Audit | None = None):
        self._client = client
        self._wanted = [c.lstrip("#") for c in channels]
        self._audit = audit
        self._ids: dict[str, str] | None = None

    def channel_ids(self) -> dict[str, str]:
        if self._ids is None:
            found, cursor = {}, None
            while True:
                res = self._client.conversations_list(types="public_channel,private_channel", limit=200,
                                                      exclude_archived=True, cursor=cursor)
                for c in res.get("channels", []):
                    if (c.get("id") in self._wanted or c.get("name") in self._wanted) and c.get("is_member"):
                        found[c["name"]] = c["id"]
                cursor = (res.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            self._ids = found
        return self._ids

    def search(self, *, since, invoice_number=None, supplier=None, amount=None, task_id=None):  # noqa: ANN001
        hits: list[ChatHit] = []
        for name, cid in self.channel_ids().items():
            res = self._client.conversations_history(channel=cid, oldest=str(since.timestamp()), limit=200)
            if self._audit:
                self._audit.record("agent", "read", f"chat:#{name}", f"history since {since:%Y-%m-%d}",
                                   task_id=task_id, policy="chat: authorized channels")
            for m in res.get("messages", []):
                text = m.get("text") or ""
                if m.get("subtype") in ("channel_join", "channel_leave"):
                    continue
                if matches(text, invoice_number=invoice_number, supplier=supplier, amount=amount):
                    link = self._client.chat_getPermalink(channel=cid, message_ts=m["ts"]).get("permalink")
                    hits.append(ChatHit(channel=name, ts=m["ts"], user=m.get("user"),
                                        user_name=(m.get("user_profile") or {}).get("real_name") or m.get("username"),
                                        text=text, permalink=link))
        return hits


class NoChat:
    name = "none"

    def search(self, **_: Any) -> list[ChatHit]:
        return []
