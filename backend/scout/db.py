"""Postgres access for Scout: connections (optionally pinned to a schema) and the schema itself."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from cfo.db.migrate import SCHEMA_PATH as COMPANY_SCHEMA

SCOUT_SCHEMA = Path(__file__).with_name("schema.sql")

ConnFactory = Callable[[], psycopg.Connection]


def connect(url: str | None = None, schema: str | None = None, *, autocommit: bool = False) -> psycopg.Connection:
    if url is None:
        from scout.config import get_scout_settings

        url = get_scout_settings().database_url
    from cfo.tenancy import connect as scoped_connect
    return scoped_connect(url, schema=schema, autocommit=autocommit)


def factory(url: str | None = None, schema: str | None = None) -> ConnFactory:
    from cfo.tenancy import bind_tenant, current_tenant, scoped_schema
    bound, name = current_tenant(required=False), scoped_schema(schema)
    # Capture at construction, not at invocation: chat/worker threads may have a different context.
    def open_connection():
        if bound is not None:
            with bind_tenant(bound):
                return connect(url, name)
        return connect(url, name)
    return open_connection


def apply_schema(conn: psycopg.Connection) -> None:
    """Company tables first, then Scout's. Both files are idempotent."""
    conn.execute(COMPANY_SCHEMA.read_text(encoding="utf-8"))
    conn.execute(SCOUT_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def notify(conn: psycopg.Connection, channel: str, payload: str) -> None:
    conn.execute("SELECT pg_notify(%s || '_' || current_schema(), %s)", (channel, payload))


def postgres_checkpointer(url: str | None = None, schema: str | None = None):  # noqa: ANN201
    """LangGraph Postgres saver: a pending approval survives a worker restart (thread id = task id)."""
    from langgraph.checkpoint.postgres import PostgresSaver

    # Tables are installed by opt-in provisioning, not by worker startup.
    return PostgresSaver(connect(url, schema, autocommit=True))
