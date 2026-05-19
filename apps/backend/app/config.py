"""Application settings (pydantic-settings). Reads .env / environment.

Secrets are never hardcoded (brief §13). Provider keys are added per-slice; the
default ``provider_mode='stub'`` needs no external accounts.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    app_name: str = "med-emergency-backend"
    log_level: str = "INFO"

    # Default to a local SQLite file so the app and tests run with zero infra;
    # docker-compose / .env override this with the Postgres URL.
    database_url: str = "sqlite:///./local.db"

    # Swappable provider gateways (see PLAN.md "Provider abstraction").
    provider_mode: str = "stub"

    # --- Auth / JWT (short-lived access + refresh; RBAC) ---
    jwt_secret: str = "change-me-generate-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 1_209_600

    # OTP login (stub SMS in dev). Codes are short-lived and hashed at rest.
    otp_ttl_seconds: int = 300
    otp_length: int = 6
    otp_max_attempts: int = 5

    # Only trust X-Forwarded-For when the app actually sits behind a trusted
    # reverse proxy (set true in that deployment). Default false so clients
    # cannot spoof the audited source IP.
    trust_forwarded_for: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
