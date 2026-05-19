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

    # Short-lived grant issued after OTP verify when no account exists yet,
    # consumed by the onboarding endpoint to create the resident.
    registration_ttl_seconds: int = 900

    # Only trust X-Forwarded-For when the app actually sits behind a trusted
    # reverse proxy (set true in that deployment). Default false so clients
    # cannot spoof the audited source IP.
    trust_forwarded_for: bool = False

    # --- Medical records storage (brief §2.3) ---
    # Requested signed-URL TTL; the gateway hard-caps it at 900s (15 min).
    s3_signed_url_ttl_seconds: int = 900
    s3_bucket: str = "med-records-local"
    s3_region: str = "ap-south-1"
    s3_endpoint_url: str | None = None  # set for LocalStack; unset for real AWS
    s3_kms_key_id: str | None = None  # SSE-KMS in prod; SSE-S3 fallback
    # Stub gateway writes encrypted blobs here (dev/CI only).
    local_storage_dir: str = "./var/records"
    # Base used to build stub signed download links.
    public_base_url: str = "http://localhost:8000"
    max_upload_bytes: int = 15_728_640  # 15 MiB


@lru_cache
def get_settings() -> Settings:
    return Settings()
