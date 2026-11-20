from typing import Annotated, Literal

from cfo.api import services
from cfo.api.deps import get_repo
from cfo.api.errors import ERRORS_422
from cfo.api.schemas import LiquidityForecast
from cfo.db.repo import Repository
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/v1", tags=["forecast"])


@router.get(
    "/forecast/liquidity",
    response_model=LiquidityForecast,
    responses=ERRORS_422,
    summary="Predicted bank balance with prediction interval and safety-buffer gaps",
)
def get_liquidity_forecast(
    horizon_days: Annotated[
        int,
        Query(
            ge=7,
            le=services.MAX_HORIZON_DAYS,
            description="Days ahead. 90 for the dashboard chart, 365 to see the June dip.",
        ),
    ] = 90,
    granularity: Annotated[
        Literal["day", "week"], Query(description="Spacing of `points`.")
    ] = "week",
    repo: Repository = Depends(get_repo),
) -> LiquidityForecast:
    """XGBoost forecast of the daily net cash flow (T015), walked forward from the current balance. `gaps` lists
    stretches below `safety_buffer` (D-013, draw it as a line); `gaps` and `lowest_point` always describe the
    whole horizon, whatever the granularity. The interval is an approximation (holdout error of the model,
    growing with the square root of the days ahead)."""
    return services.liquidity_forecast(repo, horizon_days, granularity)
