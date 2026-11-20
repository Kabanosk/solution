from fastapi import APIRouter, Depends

from cfo.api import stubs
from cfo.api.deps import mark_stub
from cfo.api.schemas import Kpis

router = APIRouter(prefix="/v1", tags=["dashboard"], dependencies=[Depends(mark_stub)])


@router.get("/kpis", response_model=Kpis, summary="Headline numbers for the dashboard")
def get_kpis() -> Kpis:
    """Balance, average monthly cash flow, the lowest forecast balance of the next 12 months, undocumented
    payments (tax risk), pending approvals and open cost anomalies."""
    return stubs.kpis()
