"""Fixed setup catalog and validated request models (the wire contract of /v1/onboarding)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StepId = Literal["bank", "invoicing", "ksef", "email", "slack", "network"]
Provider = Literal["bunq", "builtin", "ksef", "google", "slack"]
OAuthProvider = Literal["google", "slack"]

STEP_ORDER: tuple[str, ...] = ("bank", "invoicing", "ksef", "email", "slack", "network")
# step -> canonical provider id on the wire
STEP_PROVIDER: dict[str, str | None] = {"bank": "bunq", "invoicing": "builtin", "ksef": "ksef", "email": "google",
                                       "slack": "slack", "network": None}
PROVIDER_STEP: dict[str, str] = {p: s for s, p in STEP_PROVIDER.items() if p}
# step -> (connections.id, kind, provider recorded in the row)
CONNECTION: dict[str, tuple[str, str, str]] = {
    "bank": ("bank", "bank", "bunq_sandbox"),
    "invoicing": ("invoicing", "invoicing", "builtin"),
    "ksef": ("ksef", "invoicing", "ksef_test"),
    "email": ("gmail", "mail", "gmail_oauth"),
    "slack": ("slack", "chat", "slack_oauth"),
}
MANAGED_BY = "onboarding"

def app_origin(configured: str) -> str | None:
    """The configured public origin when it is a bare https origin (loopback http allowed for development)."""
    from urllib.parse import urlsplit
    try:
        u = urlsplit(configured)
        host = u.hostname or ""
        ok = (u.scheme == "https" or (u.scheme == "http" and host in ("localhost", "127.0.0.1"))) and bool(host)
        bare = not (u.username or u.password or u.query or u.fragment) and u.path == ""
        return f"{u.scheme}://{u.netloc}" if ok and bare and u.netloc == configured.split("://", 1)[1] else None
    except ValueError:
        return None


def expected_redirect(configured: str, provider: str) -> str | None:
    origin = app_origin(configured)
    return f"{origin}/api/onboarding/callback/{provider}" if origin else None


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SECRETISH = re.compile(r"-----BEGIN|PRIVATE KEY|://[^/\s]*:[^/\s@]*@", re.IGNORECASE)


def clean_text(value: str, limit: int) -> str:
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f"at most {limit} characters")
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed")
    if _SECRETISH.search(value):
        raise ValueError("secrets, keys and credentials must never be entered here")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NetworkAnswers(StrictModel):
    required: bool
    provider: str = Field(default="", max_length=200)
    protocol: str = Field(default="", max_length=200)
    systems: list[str] = Field(default_factory=list, max_length=10)
    administrator: str = Field(default="", max_length=200)
    company_only: bool = True

    @field_validator("provider", "protocol")
    @classmethod
    def _short(cls, v: str) -> str:
        return clean_text(v, 80)

    @field_validator("administrator")
    @classmethod
    def _admin(cls, v: str) -> str:
        return clean_text(v, 120)

    @field_validator("systems")
    @classmethod
    def _systems(cls, v: list[str]) -> list[str]:
        return [clean_text(item, 80) for item in v if item.strip()]


class SetupUpdate(StrictModel):
    revision: int = Field(ge=0, le=2_000_000_000)
    action: Literal["skip", "resume", "complete", "step", "network"]
    step: StepId | None = None
    state: Literal["skipped", "not_started"] | None = None
    network: NetworkAnswers | None = None

    @model_validator(mode="after")
    def _shape(self) -> SetupUpdate:
        if self.action == "step":
            if self.step is None or self.state is None or self.network is not None:
                raise ValueError("action 'step' needs step and state only")
        elif self.action == "network":
            if self.network is None or self.step is not None or self.state is not None:
                raise ValueError("action 'network' needs network only")
        elif self.step is not None or self.state is not None or self.network is not None:
            raise ValueError(f"action '{self.action}' takes no other fields")
        return self


class Empty(StrictModel):
    pass


class BunqConnect(StrictModel):
    # Sandbox keys only ("sandbox_..."). A production key is refused before anything leaves the server.
    api_key: str | None = Field(default=None, pattern=r"^sandbox_[A-Za-z0-9]{8,150}$")

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_means_create(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v


class KsefConnect(StrictModel):
    nip: str = Field(min_length=10, max_length=10, pattern=r"^\d{10}$")


class OAuthStart(StrictModel):
    redirect_uri: str = Field(min_length=1, max_length=300)
    browser_proof: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")
    label: str | None = Field(default=None, max_length=100)

    @field_validator("label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        return clean_text(v, 100) or None if v is not None else None


class OAuthCallback(StrictModel):
    state: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+$")
    code: str | None = Field(default=None, max_length=2048)
    error: str | None = Field(default=None, max_length=200)
    redirect_uri: str = Field(min_length=1, max_length=300)
    browser_proof: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")
