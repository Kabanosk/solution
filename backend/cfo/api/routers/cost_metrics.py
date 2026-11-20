from typing import Annotated

from cfo.api import services
from cfo.api.deps import get_repo
from cfo.api.errors import ERRORS_422
from cfo.api.schemas import Collection, CostMetricSeries
from cfo.db.repo import Repository
from cfo.domain import CostMetricKind
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/v1", tags=["costs"])


@router.get(
    "/cost-metrics",
    response_model=Collection[CostMetricSeries],
    responses=ERRORS_422,
    summary="The three cost-vs-metric pairs as monthly series",
)
def get_cost_metrics(
    kind: Annotated[CostMetricKind | None, Query(description="Only this pair.")] = None,
    repo: Repository = Depends(get_repo),
) -> Collection[CostMetricSeries]:
    """Food cost vs orders, ads vs new customers (CAC), app cloud vs online orders (D-006). Months where cost
    grew with no matching metric growth carry an `anomaly` explanation."""
    return Collection[CostMetricSeries](
        stub=False, items=services.cost_metric_series(repo, kind)
    )
