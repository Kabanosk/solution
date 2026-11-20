from typing import Annotated

from fastapi import APIRouter, Depends, Query

from cfo.api import stubs
from cfo.api.deps import LimitParam, OffsetParam, mark_stub
from cfo.api.errors import ERRORS_404, ERRORS_422, not_found
from cfo.api.schemas import DecisionOut, Page
from cfo.domain import DecisionStatus, PolicyLevel

router = APIRouter(prefix="/v1", tags=["decisions"], dependencies=[Depends(mark_stub)])


@router.get("/decisions", response_model=Page[DecisionOut], responses=ERRORS_422,
            summary="History of everything the agent decided or did, newest first")
def list_decisions(
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    level: Annotated[PolicyLevel | None, Query(description="AUTO, NOTIFY or APPROVAL.")] = None,
    status: Annotated[DecisionStatus | None, Query()] = None,
    action_type: Annotated[str | None, Query(description="e.g. attach_invoice.")] = None,
) -> Page[DecisionOut]:
    rows = stubs.decisions(level.value if level else None, status.value if status else None, action_type)
    return Page[DecisionOut](stub=True, items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)


@router.get("/decisions/{decision_id}", response_model=DecisionOut, responses=ERRORS_404,
            summary="One decision")
def get_decision(decision_id: str) -> DecisionOut:
    found = stubs.decision(decision_id)
    if found is None:
        raise not_found("Decision", decision_id)
    return found
