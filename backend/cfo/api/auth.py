"""DB-backed session authentication for every HTTP route, including streams and documentation."""
from __future__ import annotations

import hashlib
import re

import psycopg
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from cfo.api.errors import ApiError
from cfo.tenancy import Tenant, bind_tenant, current_tenant


def database_url() -> str:
    from cfo.config import get_settings
    return get_settings().database_url


def verify_session(token: str, slug: str) -> Tenant | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,256}", token) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", slug):
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT o.id,o.slug,o.schema_name,o.legacy,m.employee_id,m.role "
            "FROM scout_auth.sessions s JOIN scout_auth.organizations o ON o.id=s.organization_id "
            "JOIN scout_auth.memberships m ON m.organization_id=o.id AND m.user_id=s.user_id "
            "WHERE s.token_hash=%s AND s.expires_at>now() AND o.slug=%s", (digest, slug)).fetchone()
    if not row:
        return None
    try:
        return Tenant(**row)
    except (PermissionError, ValueError):
        return None


class OrganizationAuthMiddleware:
    """Pure ASGI: scope stays bound for the whole streamed response, never BaseHTTPMiddleware."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if scope["type"] == "http" and scope["path"] == "/healthz" and scope["method"] in ("GET", "HEAD"):
            return await self.app(scope, receive, send)
        headers = {}
        duplicate = False
        for key, value in scope.get("headers", []):
            key = key.lower()
            if key in (b"authorization", b"x-scout-organization"):
                if key in headers:
                    duplicate = True
                headers[key] = value.decode("latin-1")
        authorization = headers.get(b"authorization", "")
        scheme, _, token = authorization.partition(" ")
        slug = headers.get(b"x-scout-organization", "")
        tenant = None
        try:
            if not duplicate and scheme.lower() == "bearer" and token and slug:
                tenant = await run_in_threadpool(verify_session, token, slug)
        except psycopg.Error:
            return await self._error(scope, receive, send, 503, "database_unavailable", "Authentication database unavailable.")
        if tenant is None:
            return await self._error(scope, receive, send, 401, "unauthorized", "A valid session and organization are required.")
        # Global connector credentials/demo state are not organization-aware onboarding APIs.
        path = scope["path"]
        if path.startswith("/v1/demo") or (path.startswith("/v1/connections/") or
                (path.rstrip("/") == "/v1/connections" and scope.get("method") != "GET")):
            return await self._error(scope, receive, send, 403, "not_configured", "Connection mutation and demo endpoints are disabled.")
        scope.setdefault("state", {})["organization"] = tenant
        with bind_tenant(tenant):
            return await self.app(scope, receive, send)

    @staticmethod
    async def _error(scope, receive, send, status, code, message):
        if scope["type"] == "websocket":
            return await send({"type": "websocket.close", "code": 4401})
        response = JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                                headers={"WWW-Authenticate": "Bearer"} if status == 401 else {})
        return await response(scope, receive, send)


def require_write_token() -> str:
    """Compatibility dependency name; identity is the verified membership, never API_WRITE_TOKEN."""
    tenant = current_tenant(required=False)
    if tenant is None:
        raise ApiError(401, "unauthorized", "A valid organization session is required.")
    if not tenant.employee_id:
        raise ApiError(403, "not_enrolled", "Membership has no employee identity.")
    return tenant.employee_id
