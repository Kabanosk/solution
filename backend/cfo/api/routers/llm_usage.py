"""LLM usage and cost estimate (T032)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from cfo.api.deps import get_repo
from cfo.api.errors import ERRORS_422, ApiError, ErrorResponse
from cfo.api.schemas_usage import LlmUsageLive, LlmUsageReport
from cfo.db.repo import Repository
from cfo.domain import utcnow
from cfo.usage import report
from cfo.usage.settings import UsageSettings, get_usage_settings

router = APIRouter(prefix="/v1/llm", tags=["llm-usage"])

DEFAULT_RANGE = timedelta(days=7)


def get_clock() -> Callable[[], datetime]:
    """Tests replace it with `app.dependency_overrides[get_clock]`."""
    return utcnow


def get_usage_config() -> UsageSettings:
    return get_usage_settings()


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


@router.get("/usage", response_model=LlmUsageReport, response_model_by_alias=True,
            responses={**ERRORS_422}, summary="Usage and estimated cost of the LLM, per bucket and in total")
def get_llm_usage(
    from_: Annotated[datetime | None, Query(alias="from", description="Start (inclusive), ISO 8601. Default: 7 days before `to`.")] = None,
    to: Annotated[datetime | None, Query(description="End (exclusive), ISO 8601. Default: now.")] = None,
    group_by: Annotated[Literal["day", "hour", "event_type", "purpose"], Query(description="Bucket size or dimension.")] = "day",
    repo: Repository = Depends(get_repo),
    clock: Callable[[], datetime] = Depends(get_clock),
    settings: UsageSettings = Depends(get_usage_config),
) -> dict:
    """Requests, tokens, latency percentiles and cost per bucket, with two cost estimates (busy-time and
    amortized), the cost per agent action, the same tokens at hosted list prices, and the `assumptions` used."""
    now = _utc(clock())
    end = _utc(to) if to else now
    start = _utc(from_) if from_ else end - DEFAULT_RANGE
    if start >= end:
        raise ApiError(422, "validation_error", "Invalid request: `from` must be earlier than `to`.")
    return report.build_usage_report(repo.conn, start, end, group_by, now, settings)


@router.get("/usage/live", response_model=LlmUsageLive,
            responses={503: {"model": ErrorResponse, "description": "`metrics_unavailable`: no vLLM sample stored yet."}},
            summary="Latest vLLM server snapshot: load, KV cache, token rates")
def get_llm_usage_live(
    repo: Repository = Depends(get_repo),
    clock: Callable[[], datetime] = Depends(get_clock),
    settings: UsageSettings = Depends(get_usage_config),
) -> dict:
    live = report.build_live(repo.conn, _utc(clock()), settings)
    if live is None:
        raise ApiError(503, "metrics_unavailable",
                       "No vLLM metrics sampled yet: set VLLM_METRICS_URL and run `python -m cfo.usage sample`.")
    return live
