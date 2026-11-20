from typing import Annotated

from fastapi import APIRouter, Depends, Query

from cfo.api import stubs
from cfo.api.deps import mark_stub
from cfo.api.errors import ERRORS_422
from cfo.api.schemas import Collection, CostMetricSeries
from cfo.domain import CostMetricKind

router = APIRouter(prefix="/v1", tags=["costs"], dependencies=[Depends(mark_stub)])


@router.get("/cost-metrics", response_model=Collection[CostMetricSeries], responses=ERRORS_422,
            summary="The three cost-vs-metric pairs as monthly series")
def get_cost_metrics(
    kind: Annotated[CostMetricKind | None, Query(description="Only this pair.")] = None,
) -> Collection[CostMetricSeries]:
    """Food cost vs orders, ads vs new customers (CAC), app cloud vs online orders (D-006). Months where cost
    grew with no matching metric growth carry an `anomaly` explanation."""
    items = [s for s in stubs.cost_metric_series() if kind is None or s.kind == kind]
    return Collection[CostMetricSeries](stub=True, items=items)
