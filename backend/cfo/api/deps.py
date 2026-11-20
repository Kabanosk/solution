"""Shared route dependencies and query parameter types."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Query, Response

from cfo.db.repo import Repository

LimitParam = Annotated[int, Query(ge=1, le=200, description="Page size.")]
OffsetParam = Annotated[int, Query(ge=0, description="Number of items to skip.")]


def mark_stub(response: Response) -> None:
    """Router dependency: flags stub data in the `X-Stub` header. Remove it when a route serves real data."""
    response.headers["X-Stub"] = "true"


def get_repo() -> Iterator[Repository]:
    """One database connection per request. Tests replace it with `app.dependency_overrides[get_repo]`."""
    with Repository.connect() as repo:
        yield repo
