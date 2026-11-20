from typing import Annotated, Literal

from cfo.api import services
from cfo.api.auth import require_write_token
from cfo.api.deps import (ApprovalResolver, LimitParam, OffsetParam, get_agent,
                          get_repo)
from cfo.api.errors import (ERRORS_401, ERRORS_404, ERRORS_422, ApiError,
                            ErrorResponse, conflict, not_found)
from cfo.api.schemas import ApprovalAnswer, ApprovalOut, ApprovalResult, Page
from cfo.db.repo import Repository
from cfo.domain import ApprovalStatus
from fastapi import APIRouter, Depends, Query

router = APIRouter(prefix="/v1", tags=["approvals"])

ERRORS_ANSWER = {
    **ERRORS_404,
    **ERRORS_422,
    **ERRORS_401,
    403: {
        "model": ErrorResponse,
        "description": "The caller (`by`, or the CEO when a write token is used) is not "
        "the approver of this request.",
    },
    409: {
        "model": ErrorResponse,
        "description": "The request was already answered the other way.",
    },
    503: {"model": ErrorResponse, "description": "No agent runs in this API process."},
}


@router.get(
    "/approvals",
    response_model=Page[ApprovalOut],
    responses=ERRORS_422,
    summary="Approval requests (CEO decisions the agent waits for), newest first",
)
def list_approvals(
    limit: LimitParam = 50,
    offset: OffsetParam = 0,
    state: Annotated[
        Literal["pending", "resolved", "approved", "rejected"] | None,
        Query(
            description="`pending`: waiting for an answer; `resolved`: answered (approved or rejected)."
        ),
    ] = None,
    repo: Repository = Depends(get_repo),
) -> Page[ApprovalOut]:
    rows, total = services.list_approvals(repo, state=state, limit=limit, offset=offset)
    return Page[ApprovalOut](
        stub=False, items=rows, total=total, limit=limit, offset=offset
    )


@router.get(
    "/approvals/{approval_id}",
    response_model=ApprovalOut,
    responses=ERRORS_404,
    summary="One approval request with its decision",
)
def get_approval(approval_id: str, repo: Repository = Depends(get_repo)) -> ApprovalOut:
    found = services.get_approval(repo, approval_id)
    if found is None:
        raise not_found("Approval", approval_id)
    return found


def _answer(
    approval_id: str, approved: bool, by: str, repo: Repository, agent: ApprovalResolver
) -> ApprovalResult:
    try:
        result = agent.resume_approval(approval_id, approved, by)
    except KeyError:
        raise not_found("Approval", approval_id) from None
    except PermissionError:
        raise ApiError(
            403, "forbidden", f"'{by}' is not the approver of approval '{approval_id}'."
        ) from None
    state = services.get_approval(repo, approval_id)
    if state is None:
        raise not_found("Approval", approval_id)
    already = getattr(result, "status", None) == "already_resolved"
    wanted = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    if already and state.status != wanted:
        raise conflict(f"Approval '{approval_id}' was already {state.status.value}.")
    return ApprovalResult(**state.model_dump(), already_resolved=already)


@router.post(
    "/approvals/{approval_id}:approve",
    response_model=ApprovalResult,
    responses=ERRORS_ANSWER,
    dependencies=[
        Depends(require_write_token)
    ],  # first: 401 before the database or agent are touched
    summary="Approve: the agent executes the action (SIMULATED) and records the decision",
)
def approve(
    approval_id: str,
    body: ApprovalAnswer,
    repo: Repository = Depends(get_repo),
    agent: ApprovalResolver = Depends(get_agent),
    principal: str | None = Depends(require_write_token),
) -> ApprovalResult:
    return _answer(approval_id, True, principal or body.by, repo, agent)


@router.post(
    "/approvals/{approval_id}:reject",
    response_model=ApprovalResult,
    responses=ERRORS_ANSWER,
    dependencies=[
        Depends(require_write_token)
    ],  # first: 401 before the database or agent are touched
    summary="Reject: nothing is executed, the decision is recorded as rejected",
)
def reject(
    approval_id: str,
    body: ApprovalAnswer,
    repo: Repository = Depends(get_repo),
    agent: ApprovalResolver = Depends(get_agent),
    principal: str | None = Depends(require_write_token),
) -> ApprovalResult:
    return _answer(approval_id, False, principal or body.by, repo, agent)
