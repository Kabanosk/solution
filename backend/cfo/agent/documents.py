"""Turn a file found on a workstation into plain text for the extraction prompt (PDF, .eml, text)."""

from __future__ import annotations

import base64
import io
import re
from typing import Any

from cfo.agent.tools import Gateway

MAX_TEXT = 6000


def pdf_text(data: bytes) -> str:
    """Text of a PDF. pypdf first; for hand-made PDFs it cannot parse, fall back to literal strings."""
    try:
        from pypdf import PdfReader

        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages).strip()
        if text:
            return text
    except Exception:  # noqa: BLE001 - any parser failure falls through to the crude extraction
        pass
    return "\n".join(m.decode("latin-1") for m in re.findall(rb"\(([^()]*)\)", data))


def _binary_text(res: dict[str, Any]) -> str:
    if not res.get("ok") or res.get("encoding") != "base64":
        return ""
    data = base64.b64decode(res.get("content", ""))
    if res.get("content_type") == "application/pdf" or data.startswith(b"%PDF"):
        return pdf_text(data)
    return ""


def collect_text(gw: Gateway, employee: str, path: str, reason: str) -> str:
    """Readable text of `path` (email bodies include their PDF attachments). Empty string if unreadable."""
    res = gw.read_file(employee, path, reason)
    if not res.get("ok"):
        return ""
    kind = res.get("kind")
    if kind == "text":
        text = res.get("content", "")
    elif kind == "binary":
        text = _binary_text(res)
    elif kind == "email":
        parts = [f"Subject: {res.get('headers', {}).get('Subject', '')}", res.get("body", "")]
        for att in res.get("attachments", []):
            name = att.get("filename")
            if name and name.lower().endswith(".pdf"):
                parts.append(_binary_text(gw.read_attachment(employee, path, name, reason)))
        text = "\n".join(p for p in parts if p)
    else:
        text = ""
    return text[:MAX_TEXT]
