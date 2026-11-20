"""FastAPI application factory. Run: `uv run --project backend uvicorn cfo.api.app:app --port 8080`."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from cfo.api.errors import install_error_handlers
from cfo.api.routers import approvals, cost_metrics, costs, decisions, events, forecast, kpis, rules, sim
from cfo.api.schemas import Health
from cfo.api.settings import get_api_settings

VERSION = "0.1.0"

DESCRIPTION = """
REST API of the Autonomous CFO for Pizzeria Amalfi (dashboard backend).

* Versioned under `/v1`. Money is `{"amount": "1234.50", "currency": "EUR"}`, dates are ISO 8601, timestamps UTC.
* Errors always look like `{"error": {"code": "...", "message": "..."}}`.
* All read endpoints return data from the database (`"stub": false`); `"stub": true` (header `X-Stub: true`) would mark sample data.

Examples for every endpoint: `docs/api.md`.
"""

TAGS = [
    {"name": "dashboard", "description": "Headline numbers."},
    {"name": "forecast", "description": "Liquidity forecast."},
    {"name": "costs", "description": "Costs per team and cost-vs-metric pairs."},
    {"name": "decisions", "description": "What the agent decided or did, by autonomy level."},
    {"name": "rules", "description": "CEO overrides of the default policy."},
    {"name": "approvals", "description": "Requests waiting for the CEO; approve or reject."},
    {"name": "events", "description": "Live Server-Sent Events stream."},
    {"name": "simulator", "description": "Control of the December replay."},
    {"name": "system", "description": "Liveness."},
]


def create_app() -> FastAPI:
    app = FastAPI(title="Autonomous CFO API", version=VERSION, description=DESCRIPTION, openapi_tags=TAGS)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_api_settings().cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Stub"],
    )
    install_error_handlers(app)
    for module in (kpis, forecast, costs, cost_metrics, decisions, rules, approvals, events, sim):
        app.include_router(module.router)

    @app.get("/healthz", response_model=Health, tags=["system"], summary="Liveness probe (no database access)")
    def healthz() -> Health:
        return Health(status="ok", version=VERSION)

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse("/docs")

    return app


app = create_app()
