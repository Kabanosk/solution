"""`/v1/onboarding`: durable workspace setup. The organization always comes from the verified session (middleware
binds the Tenant); no request field can name another organization. Writes are owner-only."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from cfo.api.errors import ApiError
from cfo.tenancy import Tenant, current_tenant
from scout.api import Conn, Settings
from scout.onboarding import service, state
from scout.onboarding.catalog import (
    BunqConnect,
    Empty,
    KsefConnect,
    OAuthCallback,
    OAuthProvider,
    OAuthStart,
    Provider,
    SetupUpdate,
)

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def member() -> Tenant:
    return current_tenant()


def owner(request: Request) -> service.Actor:
    tenant = current_tenant()
    if tenant.role != "owner":
        raise ApiError(403, "owner_required", "Only the organization owner can change workspace setup.")
    if tenant.legacy:
        raise ApiError(403, "legacy_organization", "Setup cannot change the legacy organization's connections.")
    token = request.headers.get("authorization", "").partition(" ")[2]
    return service.Actor(tenant, hashlib.sha256(token.encode("utf-8")).hexdigest())


Reader = Annotated[Tenant, Depends(member)]
Owner = Annotated[service.Actor, Depends(owner)]
NoStore = Depends(_no_store)


@router.get("", dependencies=[NoStore], summary="Workspace setup state (derived from the tenant's real connections)")
def get_setup(conn: Conn, settings: Settings, tenant: Reader) -> dict[str, Any]:
    return state.build(conn, tenant, settings, state.fetch(conn, tenant.id))


@router.patch("", dependencies=[NoStore], summary="Record a setup decision (optimistic revision; owner only)")
def patch_setup(body: SetupUpdate, conn: Conn, settings: Settings, actor: Owner) -> dict[str, Any]:
    out = state.update(conn, actor.tenant, settings, body)
    conn.commit()
    return out


@router.post("/connect/{provider}", dependencies=[NoStore], summary="Connect a provider (owner only)")
def connect(provider: Provider, body: dict[str, Any], conn: Conn, settings: Settings, actor: Owner) -> dict[str, Any]:
    models = {"bunq": BunqConnect, "builtin": Empty, "ksef": KsefConnect, "google": OAuthStart, "slack": OAuthStart}
    try:
        parsed = models[provider].model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "body"
        raise ApiError(422, "validation_error", f"Invalid request: {where}: {first['msg']}.") from None
    handler = service.HANDLERS.get(provider)
    if handler is None:
        raise ApiError(501, "not_implemented", f"{provider} is not available yet.")
    result = handler(conn, actor, settings, parsed)
    conn.commit()
    return result or state.build(conn, actor.tenant, settings, state.fetch(conn, actor.tenant.id))


class SlackChannels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channels: list[Annotated[str, StringConstraints(pattern=r"^[CG][A-Z0-9]{8,14}$")]] = Field(max_length=20)


@router.put("/slack/channels", dependencies=[NoStore], summary="Choose the Slack channels Scout may read (owner only)")
def slack_channels(body: SlackChannels, conn: Conn, settings: Settings, actor: Owner) -> dict[str, Any]:
    service.set_slack_channels(conn, actor, settings, list(dict.fromkeys(body.channels)))
    conn.commit()
    return state.build(conn, actor.tenant, settings, state.fetch(conn, actor.tenant.id))


@router.delete("/connect/{provider}", dependencies=[NoStore], summary="Remove a connection and its stored credential")
def disconnect(provider: Provider, conn: Conn, settings: Settings, actor: Owner) -> dict[str, Any]:
    service.disconnect(conn, actor, settings, provider)
    conn.commit()
    return state.build(conn, actor.tenant, settings, state.fetch(conn, actor.tenant.id))


@router.post("/callback/{provider}", dependencies=[NoStore], summary="Finish a provider OAuth flow (owner session)")
def callback(provider: OAuthProvider, body: OAuthCallback, conn: Conn, settings: Settings, actor: Owner) -> dict[str, Any]:
    from scout.onboarding import oauth
    bound = oauth.consume(conn, settings, actor.tenant.id, actor.session_hash, provider, body.state,
                          body.redirect_uri, body.browser_proof)
    conn.commit()  # the state is spent from here on, whatever the provider says
    if bound is None:
        raise ApiError(400, "invalid_state", "The authorization link is invalid, expired or was already used. "
                                             "Start again from Scout.")
    service.CALLBACKS[provider](conn, actor, settings, body, bound)
    conn.commit()
    return state.build(conn, actor.tenant, settings, state.fetch(conn, actor.tenant.id))
