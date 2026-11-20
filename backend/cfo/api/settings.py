"""API-only settings (same `.env` as cfo.config, kept separate so other tasks do not collide on config.py)."""

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cfo.config import REPO_ROOT


DEFAULT_CEO_ID = "vincenzo"


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    # Comma-separated list of allowed browser origins, or "*" (the default, fine for the hackathon demo:
    # the API has no cookies or credentials). Env var: CORS_ORIGINS.
    cors_origins: str = Field(default="*")

    # Shared secret for the write endpoints (approve/reject, simulator controls): `Authorization: Bearer <token>`.
    # Unset or empty = the API is open (local dev). Env var: API_WRITE_TOKEN (Secret in the cluster).
    api_write_token: str | None = Field(default=None, repr=False)

    # Employee id the write token stands for: approvals are answered as this person. Env var: CEO_ID.
    ceo_id: str = Field(default=DEFAULT_CEO_ID)

    # Safety buffer for the liquidity forecast in EUR (D-013). Empty = the largest monthly fixed cost in the data.
    # Env var: SAFETY_BUFFER_EUR.
    safety_buffer_eur: Decimal | None = Field(default=None)

    @field_validator("api_write_token")
    @classmethod
    def _strip_token(cls, value: str | None) -> str | None:
        """A Secret made with `--from-file` keeps its trailing newline; blank means no token."""
        return (value or "").strip() or None

    @field_validator("ceo_id")
    @classmethod
    def _ceo_default(cls, value: str) -> str:
        """The token always stands for a real person: a blank `CEO_ID` falls back to the default."""
        return value.strip() or DEFAULT_CEO_ID

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
