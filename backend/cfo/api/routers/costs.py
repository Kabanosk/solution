from datetime import date
from typing import Annotated

from cfo.api import services
from cfo.api.deps import get_repo
from cfo.api.errors import ERRORS_422, ApiError
from cfo.api.schemas import CostsByTeam
from cfo.db.repo import Repository
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/v1", tags=["costs"])


@router.get(
    "/costs/by-team",
    response_model=CostsByTeam,
    responses=ERRORS_422,
    summary="Costs per team over a date range, with monthly breakdown",
)
def get_costs_by_team(
    from_: Annotated[
        date | None,
        Query(
            alias="from",
            description="First day, ISO date. Default: first day of the "
            "first month with bookings.",
        ),
    ] = None,
    to: Annotated[
        date | None, Query(description="Last day, ISO date. Default: last booking day.")
    ] = None,
    repo: Repository = Depends(get_repo),
) -> CostsByTeam:
    """Teams are sorted by total cost, highest first. Months are included when their first day is in range.
    Costs without a team (tax, social security) appear as team `company`."""
    if from_ and to and from_ > to:
        raise ApiError(
            422, "validation_error", "Invalid request: 'from' must not be after 'to'."
        )
    return services.costs_by_team(repo, from_, to)
