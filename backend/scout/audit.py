"""Audit log of every connector read, DM and access decision, plus the access policy (deck "Built-in IAM")."""

from __future__ import annotations

from scout.db import ConnFactory

EXEC_ROLES = frozenset({"ceo", "owner"})
FINANCE_ROLES = frozenset({"office_manager"})


def check(requester: str, role: str, target: str, resource: str) -> tuple[bool, str]:
    """May `requester` (with `role`) reach `resource` on `target`? Returns (allowed, policy).

    Targets: `laptop:<employee>`, `mail:<mailbox>`, `files:<employee>`, `chat:<channel>`, `bank`, `invoicing`.
    The CEO may reach anything; the office manager company files and mail; everyone else only their own laptop.
    """
    role = (role or "").casefold()
    if role in EXEC_ROLES:
        return True, "exec: all company data"
    kind, _, owner = target.partition(":")
    if kind in ("laptop", "files"):
        if owner == requester:
            return True, f"{owner}-devices: owner"
        if role in FINANCE_ROLES and owner != "vincenzo":
            return True, "company-folders: finance"
        return False, f"{owner}-devices: owner-or-exec-only"
    if kind in ("mail", "bank", "invoicing"):
        if role in FINANCE_ROLES:
            return True, f"{kind}: finance"
        return False, f"{kind}: finance-or-exec-only"
    if kind == "chat":
        return True, "chat: authorized channels"
    return False, "default: deny"


class Audit:
    def __init__(self, conn_factory: ConnFactory):
        self._cf = conn_factory

    def record(self, actor: str, action: str, target: str, resource: str, decision: str = "ALLOW", *,
               task_id: str | None = None, requester: str | None = None, role: str | None = None,
               policy: str | None = None, reason: str | None = None) -> None:
        with self._cf() as conn:
            conn.execute(
                "INSERT INTO audit_log (task_id, actor, requester, role, action, target, resource, decision, policy, "
                "reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (task_id, actor, requester, role, action, target, resource[:500], decision, policy, reason),
            )
            conn.commit()

    def check(self, requester: str, role: str, target: str, resource: str, *, task_id: str | None = None) -> bool:
        allowed, policy = check(requester, role, target, resource)
        self.record("agent", "access_check", target, resource, "ALLOW" if allowed else "DENY", task_id=task_id,
                    requester=requester, role=role, policy=policy)
        return allowed
