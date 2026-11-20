"""One error shape for every failure: `{"error": {"code": "...", "message": "..."}}`.

`code` is a stable machine-readable snake_case string, `message` a human-readable English sentence.
Validation errors additionally carry `details` (one entry per offending parameter).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from cfo.api.settings import get_api_settings

log = logging.getLogger("cfo.api")


class ErrorDetail(BaseModel):
    loc: list[str | int] = Field(description="Where the problem is, e.g. ['query', 'limit'].")
    message: str
    type: str = Field(description="Validator type, e.g. 'less_than_equal'.")


class ErrorBody(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["Decision 'dec_x' does not exist."])
    details: list[ErrorDetail] | None = Field(default=None, description="Only present for validation errors.")


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Raise from a route to return the standard error envelope."""

    def __init__(self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def not_found(kind: str, ident: str) -> ApiError:
    return ApiError(404, "not_found", f"{kind} '{ident}' does not exist.")


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


# Reusable `responses=` for routes, so every error shape shows up in the OpenAPI document.
ERRORS_404 = {404: {"model": ErrorResponse, "description": "Resource not found."}}
ERRORS_401 = {401: {"model": ErrorResponse, "description": "Missing or wrong write token (only when the server has `API_WRITE_TOKEN` set)."}}
ERRORS_422 = {422: {"model": ErrorResponse, "description": "Invalid query parameter or body."}}

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
}


def _envelope(status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None,
              headers: Mapping[str, str] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": body}, headers=dict(headers) if headers else None)


def cors_headers(request: Request) -> dict[str, str]:
    """CORS headers for a cross-origin request, mirroring `CORSMiddleware` (allowed origins from `CORS_ORIGINS`).

    The server-error handler runs outside the CORS middleware, so a 500 would reach the browser without them and the
    dashboard could not read the error envelope."""
    origin = request.headers.get("origin")
    if not origin:
        return {}
    allowed = get_api_settings().cors_origin_list
    headers = {"Access-Control-Expose-Headers": "X-Stub"}
    if "*" in allowed:
        return {**headers, "Access-Control-Allow-Origin": "*"}
    if origin in allowed:
        return {**headers, "Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e["loc"]), "message": str(e["msg"]), "type": str(e["type"])} for e in exc.errors()
        ]
        first = details[0] if details else None
        where = ".".join(str(p) for p in first["loc"]) if first else "request"
        message = f"Invalid request: {where}: {first['message']}." if first else "Invalid request."
        return _envelope(422, "validation_error", message, details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
        message = {404: "No such endpoint."}.get(exc.status_code, str(exc.detail))
        return _envelope(exc.status_code, code, message, headers=exc.headers)  # keeps `Allow` of a 405

    @app.exception_handler(psycopg.OperationalError)
    async def _database(_: Request, exc: Exception) -> JSONResponse:
        log.error("database unavailable: %s", exc)
        return _envelope(503, "database_unavailable", "The database is not reachable.")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return _envelope(500, "internal_error", "Internal server error.", headers=cors_headers(request))
