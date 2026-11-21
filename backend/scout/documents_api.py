"""`GET /v1/documents/{id}`: the original of a flagged document, for the owner who decides on it (T052).

Strict on purpose: it needs `API_WRITE_TOKEN` to be configured (unset = the endpoint is off, never open), the document
is served only while its approval is pending or after it was approved (never after a rejection), as a download and
never rendered, and every request is audited."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from cfo.api.auth import require_write_token
from cfo.api.errors import ApiError
from cfo.api.settings import get_api_settings
from scout.api import Conn

router = APIRouter(prefix="/v1", tags=["invoices"])


def _owner_token(by: Annotated[str | None, Depends(require_write_token)]) -> str:
    if not get_api_settings().api_write_token:  # fail closed: without a configured token nobody may read these
        raise ApiError(503, "not_configured", "Flagged documents are served only when API_WRITE_TOKEN is set.")
    return by or "owner"


def _audit(conn: Any, doc_id: int, decision: str, who: str, task_id: str | None, why: str) -> None:
    conn.execute("INSERT INTO audit_log (task_id, actor, requester, action, target, resource, decision, policy, reason) "
                 "VALUES (%s, 'dashboard', %s, 'download_flagged_document', 'documents', %s, %s, "
                 "'guardrails: flagged document', %s)", (task_id, who, f"document {doc_id}", decision, why))
    conn.commit()


@router.get("/documents/{document_id}", summary="The original of a flagged document (owner token, audited)")
def flagged_document(document_id: int, conn: Conn, who: Annotated[str, Depends(_owner_token)]) -> Response:
    row: dict[str, Any] | None = conn.execute(
        "SELECT d.name, d.content, d.task_id, (SELECT a.status FROM approvals a WHERE a.task_id = d.task_id "
        "ORDER BY a.created_at DESC LIMIT 1) AS approval FROM flagged_documents d WHERE d.id = %s",
        (document_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", f"No document {document_id}.")
    if row["approval"] not in ("pending", "approved"):
        _audit(conn, document_id, "DENY", who, row["task_id"], f"approval {row['approval'] or 'missing'}")
        raise ApiError(403, "not_available", "This document is not available (its approval was rejected or is missing).")
    _audit(conn, document_id, "ALLOW", who, row["task_id"], f"approval {row['approval']}")
    name = "".join(c for c in row["name"] if c.isprintable() and c not in '"\\/;') or "document"
    ascii_name = name.encode("ascii", "ignore").decode().strip() or "document"
    return Response(bytes(row["content"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; "
                                                    f"filename*=UTF-8''{quote(name)}",
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
