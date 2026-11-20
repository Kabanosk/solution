"""Shared route dependencies and query parameter types."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, Response

LimitParam = Annotated[int, Query(ge=1, le=200, description="Page size.")]
OffsetParam = Annotated[int, Query(ge=0, description="Number of items to skip.")]


def mark_stub(response: Response) -> None:
    """Router dependency: flags stub data in the `X-Stub` header. Remove it when a route serves real data."""
    response.headers["X-Stub"] = "true"
