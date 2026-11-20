"""REST API for the dashboard (FastAPI, OpenAPI at /openapi.json, docs in docs/api.md).

Layout:
  cfo.api.app          create_app(): CORS, error handlers, routers (plus Scout's `scout.api`), /healthz
  cfo.api.errors       ApiError and the `{"error": {"code", "message"}}` envelope
  cfo.api.schemas      response models (Money, Page, ...), the contract with the front
  cfo.api.services     read-side logic: database -> response models (kpis, forecast, costs, cost metrics)
  cfo.api.routers      one module per resource, so tasks that wire a resource to the DB touch separate files
"""
