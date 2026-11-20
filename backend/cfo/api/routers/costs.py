from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from cfo.api import stubs
from cfo.api.deps import mark_stub
from cfo.api.errors import ERRORS_422, ApiError
from cfo.api.schemas import CostsByTeam

router = APIRouter(prefix="/v1", tags=["costs"], dependencies=[Depends(mark_stub)])


@router.get("/costs/by-team", response_model=CostsByTeam, responses=ERRORS_422,
            summary="Costs per team over a date range, with monthly breakdown")
def get_costs_by_team(
    from_: Annotated[date | None, Query(alias="from", description="First day, ISO date. Default 2025-01-01.")] = None,
    to: Annotated[date | None, Query(description="Last day, ISO date. Default 2025-12-31.")] = None,
) -> CostsByTeam:
    """Teams are sorted by total cost, highest first. Months are included when their first day is in range."""
    if from_ and to and from_ > to:
        raise ApiError(422, "validation_error", "Invalid request: 'from' must not be after 'to'.")
    return stubs.costs_by_team(from_, to)
