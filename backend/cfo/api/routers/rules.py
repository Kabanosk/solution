from typing import Annotated

from fastapi import APIRouter, Depends, Query

from cfo.api import services
from cfo.api.deps import LimitParam, OffsetParam, get_repo
from cfo.api.errors import ERRORS_422
from cfo.api.schemas import Page, RuleOut
from cfo.db.repo import Repository

router = APIRouter(prefix="/v1", tags=["rules"])


@router.get("/rules", response_model=Page[RuleOut], responses=ERRORS_422,
            summary="Policy rules the CEO set with a sentence, newest first")
def list_rules(
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    active: Annotated[bool | None, Query(description="Only active (true) or only switched-off (false) rules.")] = None,
    repo: Repository = Depends(get_repo),
) -> Page[RuleOut]:
    rows = services.list_rules(repo, active=active)
    return Page[RuleOut](stub=False, items=rows[offset:offset + limit], total=len(rows), limit=limit, offset=offset)
