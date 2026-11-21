"""FastAPI application factory. Run: `uv run --project backend uvicorn cfo.api.app:app --port 8080`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from cfo.api.errors import install_error_handlers
from cfo.api.routers import cost_metrics, costs, forecast, kpis, llm_usage
from cfo.api.schemas import Health
from cfo.api.settings import get_api_settings
from scout.api import router as scout_router
from scout.chat_api import router as scout_chat_router
from scout.documents_api import router as documents_router
from scout.ksef_api import router as ksef_router
from scout.onboarding.api import router as onboarding_router

VERSION = "0.1.0"

DESCRIPTION = """
REST API of Scout, the finance agent of Pizzeria Amalfi (dashboard backend).

* Versioned under `/v1`. Money is `{"amount": "1234.50", "currency": "EUR"}`, dates are ISO 8601, timestamps UTC.
* Errors always look like `{"error": {"code": "...", "message": "..."}}`.
* All read endpoints return data from the database (`"stub": false`); `"stub": true` (header `X-Stub: true`) would mark sample data.

Examples for every endpoint: `docs/api.md`.
"""

TAGS = [
    {"name": "dashboard", "description": "Headline numbers."},
    {"name": "forecast", "description": "Liquidity forecast."},
    {"name": "costs", "description": "Costs per team and cost-vs-metric pairs."},
    {"name": "tasks", "description": "Scout's agent tasks, their step log and a live stream of changes."},
    {"name": "approvals", "description": "Requests waiting for a person; approve or reject (the worker resumes the task)."},
    {"name": "audit", "description": "Every connector read, DM and access decision."},
    {"name": "invoices", "description": "The invoice filed for a bank booking, with line items and document text."},
    {"name": "connections", "description": "Systems Scout reads (bank, mail, files, Slack) and their status."},
    {"name": "onboarding", "description": "Workspace setup: durable checklist and tenant-bound provider connections."},
    {"name": "demo", "description": "Demo triggers (bank mock payments)."},
    {"name": "chat", "description": "Ask Scout in the dashboard (tools, streamed steps)."},
    {"name": "llm-usage", "description": "What the LLM costs: usage per call and the vLLM server's load."},
    {"name": "system", "description": "Liveness."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The API runs no agent code: Scout's worker pool (`python -m scout.worker`) does."""
    # Global MLflow autologging is not tenant aware; do not enable it in the multi-tenant API.
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Scout API", version=VERSION, description=DESCRIPTION, openapi_tags=TAGS,
                  lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_api_settings().cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Stub"],
    )
    from cfo.api.auth import OrganizationAuthMiddleware
    app.add_middleware(OrganizationAuthMiddleware)
    install_error_handlers(app)
    for module in (kpis, forecast, costs, cost_metrics, llm_usage):
        app.include_router(module.router)
    app.include_router(onboarding_router)
    app.include_router(ksef_router)  # before Scout's router: /v1/demo/play/ksef beats /v1/demo/play/{scenario}
    app.include_router(scout_router)
    app.include_router(documents_router)
    app.include_router(scout_chat_router)

    @app.get("/healthz", response_model=Health, tags=["system"], summary="Liveness probe (no database access)")
    def healthz() -> Health:
        return Health(status="ok", version=VERSION)

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/docs")

    original_openapi = app.openapi

    def authenticated_openapi():
        document = original_openapi()
        document.setdefault("components", {}).setdefault("securitySchemes", {}).update({
            "SessionToken": {"type": "http", "scheme": "bearer", "description": "Opaque DB-backed session token."},
            "Organization": {"type": "apiKey", "in": "header", "name": "X-Scout-Organization"},
        })
        document["security"] = [{"SessionToken": [], "Organization": []}]
        document["paths"]["/healthz"]["get"]["security"] = []
        return document

    app.openapi = authenticated_openapi
    return app


app = create_app()
