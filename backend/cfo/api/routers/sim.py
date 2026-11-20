from cfo.api.auth import require_write_token
from cfo.api.deps import SimControl, get_sim_control
from cfo.api.errors import ERRORS_401, ERRORS_422
from cfo.api.schemas import EventOut, SimStart, SimStatusOut, SimStepResult
from cfo.sim.engine import SimStatus
from fastapi import APIRouter, Body, Depends

router = APIRouter(prefix="/v1", tags=["simulator"])

NOTE = (
    "Events written by `step` go to the agent subscribers of *this* API process (`cfo.sim.subscribe`); `start` only "
    "flips the shared state, the clock is advanced by the worker process that runs `python -m cfo.sim run` "
    "(subscribers live there). See docs/api.md."
)


def _status(s: SimStatus) -> SimStatusOut:
    return SimStatusOut(**s.model_dump())


@router.get("/sim", response_model=SimStatusOut, summary="State of the December replay")
def sim_status(sim: SimControl = Depends(get_sim_control)) -> SimStatusOut:
    return _status(sim.status())


@router.post(
    "/sim:start",
    response_model=SimStatusOut,
    responses={**ERRORS_401, **ERRORS_422},
    dependencies=[Depends(require_write_token)],
    summary="Start or resume the replay",
    description=NOTE,
)
def sim_start(
    body: SimStart | None = Body(default=None),
    sim: SimControl = Depends(get_sim_control),
) -> SimStatusOut:
    return _status(sim.start(body.speed if body else None))


@router.post(
    "/sim:pause",
    response_model=SimStatusOut,
    responses=ERRORS_401,
    dependencies=[Depends(require_write_token)],
    summary="Pause the replay",
)
def sim_pause(sim: SimControl = Depends(get_sim_control)) -> SimStatusOut:
    return _status(sim.pause())


@router.post(
    "/sim:step",
    response_model=SimStepResult,
    responses=ERRORS_401,
    dependencies=[Depends(require_write_token)],
    summary="Emit the next event now",
    description=NOTE,
)
def sim_step(sim: SimControl = Depends(get_sim_control)) -> SimStepResult:
    event = sim.step()
    return SimStepResult(
        event=(
            EventOut(id=event.id, ts=event.ts, kind=event.kind, payload=event.payload)
            if event
            else None
        ),
        status=_status(sim.status()),
    )
