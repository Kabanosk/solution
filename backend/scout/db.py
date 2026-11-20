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
    kwargs: dict = {"row_factory": dict_row, "autocommit": autocommit}
    if schema:
        kwargs["options"] = f"-csearch_path={schema}"
    return psycopg.connect(url, **kwargs)


def factory(url: str | None = None, schema: str | None = None) -> ConnFactory:
    return lambda: connect(url, schema)


def apply_schema(conn: psycopg.Connection) -> None:
    """Company tables first, then Scout's. Both files are idempotent."""
    conn.execute(COMPANY_SCHEMA.read_text(encoding="utf-8"))
    conn.execute(SCOUT_SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def notify(conn: psycopg.Connection, channel: str, payload: str) -> None:
    conn.execute("SELECT pg_notify(%s, %s)", (channel, payload))


def postgres_checkpointer(url: str | None = None, schema: str | None = None):  # noqa: ANN201
    """LangGraph Postgres saver: a pending approval survives a worker restart (thread id = task id)."""
    from langgraph.checkpoint.postgres import PostgresSaver

    saver = PostgresSaver(connect(url, schema, autocommit=True))
    saver.setup()
    return saver
