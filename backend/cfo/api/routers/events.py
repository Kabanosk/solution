from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse

from cfo.api import stream
from cfo.api.deps import StreamConfig, get_stream_config

router = APIRouter(prefix="/v1", tags=["events"])

SSE_DESCRIPTION = """\
Server-Sent Events (`text/event-stream`). Messages:

* `event: decision`, `data` = a decision like `GET /v1/decisions/{id}`;
* `event: approval`, `data` = an approval like `GET /v1/approvals/{id}` (sent when created and again when answered);
* `event: event`, `data` = a simulator event (`id`, `ts`, `kind`, `payload`).

The first frame (`retry` and `: connected`) carries an `id:` too, the position the stream starts at. Every message has an `id:` that is a resume token: after a disconnect `EventSource` sends it back as `Last-Event-ID`
and receives everything newer. Without it the stream starts from now (nothing is replayed). The server polls the
database about once a second and sends a `: keepalive` comment when nothing happens.
"""


@router.get("/events", summary="Live stream of new decisions, approvals and events (SSE)", description=SSE_DESCRIPTION,
            response_class=StreamingResponse,
            responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}},
                             "description": "An endless `text/event-stream`."}})
async def events(
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID", description="Resume token (set by EventSource).")] = None,
    last_event_id_query: Annotated[str | None, Query(alias="last_event_id", description="Same as the header, for clients that cannot set it.")] = None,
    config: StreamConfig = Depends(get_stream_config),
) -> StreamingResponse:
    return StreamingResponse(
        stream.stream(config, last_event_id or last_event_id_query),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
