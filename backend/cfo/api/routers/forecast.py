from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from cfo.api import stubs
from cfo.api.deps import mark_stub
from cfo.api.errors import ERRORS_422
from cfo.api.schemas import LiquidityForecast

router = APIRouter(prefix="/v1", tags=["forecast"], dependencies=[Depends(mark_stub)])


@router.get("/forecast/liquidity", response_model=LiquidityForecast, responses=ERRORS_422,
            summary="Predicted bank balance with prediction interval and negative-balance gaps")
def get_liquidity_forecast(
    horizon_days: Annotated[int, Query(ge=7, le=stubs.MAX_HORIZON_DAYS,
                                       description="Days ahead. 90 for the dashboard chart, 365 to see the June gap.")] = 90,
    granularity: Annotated[Literal["day", "week"], Query(description="Spacing of `points`.")] = "week",
) -> LiquidityForecast:
    """`gaps` and `lowest_point` always describe the whole horizon, whatever the granularity."""
    return stubs.liquidity_forecast(horizon_days, granularity)
