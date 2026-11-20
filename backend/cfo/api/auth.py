"""Write protection: a shared bearer token for the endpoints that change state.

`API_WRITE_TOKEN` unset (or empty) leaves the API open, which is what local development and the dashboard team's
mock setups rely on. When it is set, approve/reject and the simulator controls need
`Authorization: Bearer <API_WRITE_TOKEN>`; reads, `/healthz` and the SSE stream stay open. Slack approvals do not
go through the API (the agent receives them directly), so they are not affected.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from cfo.api.errors import ApiError
from cfo.api.settings import get_api_settings

bearer = HTTPBearer(
    auto_error=False, scheme_name="WriteToken",
    description="Shared secret `API_WRITE_TOKEN`. Required on write endpoints only when the server has it set.")


def require_write_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> str | None:
    """Router dependency of the write endpoints. Returns the employee id the token stands for (the CEO), or None
    when no token is configured (the caller then names itself, see `ApprovalAnswer.by`)."""
    settings = get_api_settings()
    expected = settings.api_write_token
    if not expected:
        return None
    supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "This endpoint needs 'Authorization: Bearer <token>'.",
                       headers={"WWW-Authenticate": "Bearer"})
    return settings.ceo_id
