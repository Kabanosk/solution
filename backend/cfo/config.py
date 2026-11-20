"""Settings from environment variables or a `.env` file at the repository root (see `.env.example`)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All runtime configuration. Field `database_url` is read from `DATABASE_URL`, and so on."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://cfo:cfo@localhost:5433/cfo"
    # OpenAI-compatible endpoint: vLLM directly, or a NeMo Guardrails proxy at the same URL (D-009).
    llm_base_url: str = "http://localhost:8000/v1"
    llm_model: str = "llama-3.2-3b"
    llm_api_key: str = "EMPTY"  # vLLM accepts any non-empty key
    # TLS check for LLM_BASE_URL: "true" (system CAs), "false" (off), or a path to a CA bundle
    # (on OpenShift: the injected service CA, e.g. /etc/llm-ca/service-ca.crt).
    llm_verify_tls: str = "true"
    slack_bot_token: str = ""  # xoxb-...
    slack_app_token: str = ""  # xapp-... (Socket Mode)
    slack_user_map: str = ""  # employee_id=SLACK_USER_ID pairs, comma separated; overrides HR data
    # Root of the simulated company computers: <root>/<employee>/{Downloads,Mail,...}
    workstations_root: Path = Field(default=Path("data/generated/workstations"))
    # Simulator speed multiplier: 1.0 = simulated time runs at real time, 3600 = one hour per second.
    sim_speed: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
