"""Immutable verified organization scope. No implicit public tenant, including in background threads."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

LEGACY_ID = "00000000-0000-4000-8000-000000000001"
LEGACY_SLUG = "pizzeria-amalfi"


@dataclass(frozen=True)
class Tenant:
    id: str
    slug: str
    schema_name: str
    employee_id: str | None
    role: str
    legacy: bool = False

    def __post_init__(self) -> None:
        oid = str(UUID(str(self.id)))
        object.__setattr__(self, "id", oid)
        reserved = oid == LEGACY_ID
        if reserved:
            valid = self.legacy and self.schema_name == "public" and self.slug == LEGACY_SLUG
        else:
            valid = not self.legacy and self.schema_name == "tenant_" + UUID(oid).hex and self.slug != LEGACY_SLUG
        if not valid:
            raise PermissionError("Invalid organization schema registration")


_current: ContextVar[Tenant | None] = ContextVar("scout_organization", default=None)


def current_tenant(*, required: bool = True) -> Tenant | None:
    tenant = _current.get()
    if required and tenant is None:
        raise PermissionError("No verified organization bound")
    return tenant


@contextmanager
def bind_tenant(tenant: Tenant) -> Iterator[Tenant]:
    token = _current.set(tenant)
    try:
        yield tenant
    finally:
        _current.reset(token)


def scoped_schema(schema: str | None = None) -> str:
    tenant = current_tenant(required=False)
    if tenant:
        if schema is not None and schema != tenant.schema_name:
            raise PermissionError("Cross-organization schema access")
        return tenant.schema_name
    # Explicit schemas are for operator/test utilities only, never inferred from an environment URL.
    if schema is None:
        raise PermissionError("No verified organization bound")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Unsafe schema identifier")
    return schema


def connect(database_url: str | None = None, *, schema: str | None = None, **kwargs) -> psycopg.Connection:
    from cfo.config import get_settings
    name = scoped_schema(schema)
    kwargs.setdefault("row_factory", dict_row)
    kwargs["options"] = f"-csearch_path={name}"
    return psycopg.connect(database_url or get_settings().database_url, **kwargs)


def legacy_connectors_enabled(tenant: Tenant | None = None) -> bool:
    tenant = tenant or current_tenant(required=False)
    return bool(tenant and tenant.legacy and tenant.id == LEGACY_ID and
                os.getenv("SCOUT_LEGACY_CONNECTORS_ORGANIZATION") == LEGACY_ID)


def worker_tenant(database_url: str) -> Tenant:
    organization_id = os.getenv("SCOUT_ORGANIZATION_ID", "")
    if not organization_id:
        raise PermissionError("Set SCOUT_ORGANIZATION_ID to an explicitly enrolled organization UUID")
    try:
        oid = UUID(organization_id)
    except ValueError:
        raise PermissionError("Invalid worker organization UUID") from None
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT o.id,o.slug,o.schema_name,o.legacy, "
            "(SELECT m.employee_id FROM scout_auth.memberships m WHERE m.organization_id=o.id "
            "AND m.role='owner' ORDER BY m.user_id LIMIT 1) AS employee_id "
            "FROM scout_auth.organizations o WHERE o.id=%s", (oid,)).fetchone()
    if not row or not row["employee_id"]:
        raise PermissionError("Worker organization has no explicitly enrolled owner")
    return Tenant(**row, role="worker")
