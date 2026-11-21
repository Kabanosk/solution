"""Scout settings from the environment or the repository `.env` (see `.env.example`)."""

from __future__ import annotations

from functools import lru_cache
from decimal import Decimal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cfo.config import REPO_ROOT


class ScoutSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql://cfo:cfo@localhost:5434/cfo"
    scout_workers: int = 4
    strategy_operating_surplus_target: Decimal | None = Field(default=None, allow_inf_nan=False)
    strategy_owner_income_target: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    strategy_currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    strategy_monthly_revenue_target: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    reminder_after_hours: float = 24.0
    chat_lookback_days: int = 30
    chat_channels: list[str] = Field(default_factory=lambda: ["finance"])
    bank_api_url: str = "http://localhost:8010"
    bank_poll_seconds: float = 5.0
    mail_poll_seconds: float = 30.0
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/v1/connections/google/callback"
    google_mail_label: str = "Amalfi"
    connections_key: str = ""  # Fernet key (urlsafe base64, 32 bytes)
    oauth_state_secret: str = ""
    # Dedicated provider app registrations for workspace onboarding (server-side only; never identity sign-in credentials).
    scout_app_origin: str = ""  # same public origin as the dashboard; OAuth redirects must equal it + /api/onboarding/callback/<provider>
    slack_client_id: str = ""
    slack_client_secret: str = ""
    dashboard_url: str = "http://127.0.0.1:3000"
    scout_icon_url: str = ""  # public PNG for the Slack avatar; default: <dashboard_url>/scout-icon.png on https
    ceo_id: str = "vincenzo"
    document_owner_id: str = "grazyna"
    workstations_root: str = "data/generated/workstations"
    data_dir: str = "data/generated"
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_user_map: str = ""
    api_write_token: str = ""
    scout_demo: bool = False
    guardrails_url: str = ""  # NeMo Guardrails check service; empty = safety checks off (D-029)
    guardrails_token: str = ""
    guardrails_ca: str = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"  # OpenShift service CA
    compliance_contact: str = ""  # employee id or Slack user id told about repeated blocked prompts; empty = the CEO
    ksef_api_url: str = "https://api-test.ksef.mf.gov.pl/v2"  # KSeF test environment (self-signed seals allowed)

    @field_validator("chat_channels", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip().startswith("["):
            return [c.strip().lstrip("#") for c in v.split(",") if c.strip()]
        return v


def tenant_settings(settings: ScoutSettings) -> ScoutSettings:
    """Never hand global integration credentials or seeded identities to a new organization."""
    from cfo.tenancy import current_tenant, legacy_connectors_enabled
    tenant = current_tenant()
    if legacy_connectors_enabled(tenant):
        return settings
    # Kept: the server's OAuth app registrations and sealing master secret (app-level, no tenant data). Everything that
    # is a global bot token, channel, mailbox label, file root or seeded identity is still removed.
    return settings.model_copy(update={
        "slack_bot_token": "", "slack_app_token": "", "slack_user_map": "", "chat_channels": [],
        "google_mail_label": "", "oauth_state_secret": "",
        "bank_api_url": "", "workstations_root": "", "data_dir": "", "scout_demo": False,
        "strategy_operating_surplus_target": None, "strategy_owner_income_target": None,
        "strategy_monthly_revenue_target": None, "strategy_currency": "EUR",
        "ceo_id": tenant.employee_id or "", "document_owner_id": tenant.employee_id or "",
    })


@lru_cache
def get_scout_settings() -> ScoutSettings:
    return ScoutSettings()
