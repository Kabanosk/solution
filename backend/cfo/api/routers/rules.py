from typing import Annotated

from fastapi import APIRouter, Depends, Query

from cfo.api import stubs
from cfo.api.deps import LimitParam, OffsetParam, mark_stub
from cfo.api.errors import ERRORS_422
from cfo.api.schemas import Page, RuleOut

router = APIRouter(prefix="/v1", tags=["rules"], dependencies=[Depends(mark_stub)])


@router.get("/rules", response_model=Page[RuleOut], responses=ERRORS_422,
            summary="Policy rules the CEO set with a sentence, newest first")
def list_rules(
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    active: Annotated[bool | None, Query(description="Only active (true) or only switched-off (false) rules.")] = None,
) -> Page[RuleOut]:
    rows = stubs.rules(active)
    return Page[RuleOut](stub=True, items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)
