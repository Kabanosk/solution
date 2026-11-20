"""Turn a document (PDF bytes, or text) into plain text for the extraction prompt."""

from __future__ import annotations

import base64
import io
import re
from typing import Any


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


def document_text(data: bytes, name: str = "") -> str:
    """Text of a PDF (by magic bytes or name) or of a UTF-8 text file; '' when it is neither."""
    if data[:5] == b"%PDF-" or name.lower().endswith(".pdf"):
        return pdf_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""
