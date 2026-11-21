"""`GET /v1/documents/{id}`: the original of a flagged document that was attached to an approved invoice (T052).
Write token only: the content was flagged by the guardrails and is served as a download, never rendered."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import Response

from cfo.api.errors import ApiError
from scout.api import Conn, WriteAuth

router = APIRouter(prefix="/v1", tags=["invoices"])


@router.get("/documents/{document_id}", summary="The original of a flagged document attached to an approved invoice")
def flagged_document(document_id: int, conn: Conn, _: WriteAuth) -> Response:
    row: dict[str, Any] | None = conn.execute("SELECT name, content FROM flagged_documents WHERE id = %s",
                                              (document_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", f"No document {document_id}.")
    name = row["name"].replace('"', "").replace("\n", " ")
    return Response(bytes(row["content"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"',
                             "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
