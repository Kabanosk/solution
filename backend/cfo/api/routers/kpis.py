from cfo.api import services
from cfo.api.deps import get_repo
from cfo.api.schemas import Kpis
from cfo.db.repo import Repository
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/v1", tags=["dashboard"])


@router.get("/kpis", response_model=Kpis, summary="Headline numbers for the dashboard")
def get_kpis(repo: Repository = Depends(get_repo)) -> Kpis:
    """Balance, average monthly cash flow, the lowest forecast balance of the next 12 months, undocumented
    payments (tax risk), pending approvals and open cost anomalies."""
    return services.kpis(repo)
