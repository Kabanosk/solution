"""Company laptops: company folders only (Downloads, Mail, Documents), every access in the audit log."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Protocol

from cfo.mcp_servers.workstations import WorkstationTools
from scout.audit import Audit


class FilesConnector(Protocol):
    name: str

    def search(self, owner: str, terms: list[str], *, task_id: str | None = None) -> list[dict]: ...

    def read(self, owner: str, path: str, *, task_id: str | None = None) -> bytes | None: ...


class _AuditLog:
    """`WorkstationTools` access logger that writes Scout's audit log."""

    def __init__(self, audit: Audit):
        self.audit = audit
        self.task_id: str | None = None

    def log(self, employee: str, action: str, path: str, reason: str | None) -> None:
        denied = action.startswith("DENIED")
        self.audit.record("agent", action.split()[1] if denied else action.split()[0], f"files:{employee}",
                          path or "*", "DENY" if denied else "ALLOW", task_id=self.task_id, reason=reason,
                          policy="company-folders-only" if denied else None)


class WorkstationFiles:
    name = "workstation"

    def __init__(self, root: Path | str, audit: Audit):
        self._log = _AuditLog(audit)
        self._tools = WorkstationTools(root, self._log)

    def search(self, owner: str, terms: list[str], *, task_id: str | None = None) -> list[dict]:
        self._log.task_id = task_id
        seen: dict[str, dict] = {}
        for term in [t for t in terms if t]:
            res = self._tools.search_files(owner, term, reason="find a document for a payment")
            for f in res.get("files", []) if res.get("ok") else []:
                seen.setdefault(f["path"], f)
        return list(seen.values())

    def read(self, owner: str, path: str, *, task_id: str | None = None) -> bytes | None:
        self._log.task_id = task_id
        res = self._tools.read_file(owner, path, reason="read a candidate document")
        if not res.get("ok"):
            return None
        if res.get("kind") == "binary":
            return base64.b64decode(res["content"])
        if res.get("kind") == "email":
            for att in res.get("attachments", []):
                name = att.get("filename") if isinstance(att, dict) else att
                if name and name.lower().endswith(".pdf"):
                    a = self._tools.read_attachment(owner, path, name, reason="read the attached invoice")
                    if a.get("ok"):
                        return base64.b64decode(a["content"])
            return (res.get("body") or "").encode()
        return (res.get("content") or "").encode()
