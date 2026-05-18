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


@lru_cache
def get_settings() -> Settings:
    return Settings()
