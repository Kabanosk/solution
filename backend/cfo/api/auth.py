import hmac
from typing import Annotated

from cfo.api.errors import ApiError
from cfo.api.settings import get_api_settings
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False, scheme_name="WriteToken")


def require_write_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> str | None:
    settings = get_api_settings()
    expected = settings.api_write_token
    if not expected:
        return None
    supplied = (
        credentials.credentials
        if credentials and credentials.scheme.lower() == "bearer"
        else ""
    )
    if not hmac.compare_digest(
        supplied.encode(), expected.encode()
    ):  # porównanie w stałym czasie
        raise ApiError(
            401,
            "unauthorized",
            "This endpoint needs 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return settings.ceo_id
