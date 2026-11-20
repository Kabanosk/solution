"""Shared helpers for the MCP servers."""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import quote

from cfo.config import get_settings
from cfo.db.repo import Repository


def make_repo_factory(database_url: str | None = None, schema: str | None = None):
    """Return a no-arg callable opening a fresh `Repository` (one short connection per tool call).

    `schema` pins `search_path` (tests use an isolated schema in the compose Postgres).
    """
    url = database_url or get_settings().database_url
    if schema:
        url += ("&" if "?" in url else "?") + "options=" + quote(f"-csearch_path={schema}")

    def factory() -> Repository:
        return Repository.connect(url)

    return factory


def to_data(model: Any) -> Any:
    """Pydantic model (or list of them) to JSON-able data."""
    if isinstance(model, list):
        return [to_data(m) for m in model]
    return model.model_dump(mode="json")


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, date) else date.fromisoformat(value)
