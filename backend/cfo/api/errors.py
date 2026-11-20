"""One error shape for every failure: `{"error": {"code": "...", "message": "..."}}`.

`code` is a stable machine-readable snake_case string, `message` a human-readable English sentence.
Validation errors additionally carry `details` (one entry per offending parameter).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

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

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def not_found(kind: str, ident: str) -> ApiError:
    return ApiError(404, "not_found", f"{kind} '{ident}' does not exist.")


def conflict(message: str) -> ApiError:
    return ApiError(409, "conflict", message)


# Reusable `responses=` for routes, so every error shape shows up in the OpenAPI document.
ERRORS_404 = {404: {"model": ErrorResponse, "description": "Resource not found."}}
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


def _envelope(status_code: int, code: str, message: str, details: list[dict[str, Any]] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return JSONResponse(status_code=status_code, content={"error": body})


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc.status_code, exc.code, exc.message)

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
        return _envelope(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return _envelope(500, "internal_error", "Internal server error.")
