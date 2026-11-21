"""Explicit established-session seam for existing route unit tests.

These tests stub the database/repository and test route behavior, not authentication.
Full session/membership, expiry, tenant and stream isolation run unmocked in test_tenant_isolation.py.
"""
from fastapi.testclient import TestClient as StarletteTestClient
import psycopg
from psycopg.rows import dict_row

from cfo.api.auth import OrganizationAuthMiddleware
from cfo.db.repo import Repository


class TestClient(StarletteTestClient):
    __test__ = False

    def __init__(self, app, *args, **kwargs):
        if not any(m.cls is OrganizationAuthMiddleware for m in app.user_middleware):
            app.add_middleware(OrganizationAuthMiddleware)
        kwargs.setdefault("headers", {"Authorization": "Bearer tok", "X-Scout-Organization": "pizzeria-amalfi"})
        super().__init__(app, *args, **kwargs)


def operator_repository(database_url):
    """Local test fixture admin; intentional raw DDL access, never a production request factory."""
    if not ("127.0.0.1" in database_url or "localhost" in database_url):
        raise RuntimeError("Fixture admin requires a disposable local database")
    return Repository(psycopg.connect(database_url, row_factory=dict_row))
