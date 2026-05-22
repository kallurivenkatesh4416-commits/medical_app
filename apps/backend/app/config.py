"""Application settings (pydantic-settings). Reads .env / environment.

Secrets are never hardcoded (brief §13). Provider keys are added per-slice; the
default ``provider_mode='stub'`` needs no external accounts.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-generate-a-long-random-string"


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

    # --- Notification providers (Slice 6+; only needed when provider_mode=live) ---
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_sms_from: str | None = None
    twilio_voice_from: str | None = None
    # Slice 16: when set, the live Twilio gateway passes this URL as the
    # `StatusCallback` on every send so the provider posts delivery state
    # back to us. The webhook handler is signature-validated; leaving this
    # blank disables callbacks entirely (the existing `sent` status from
    # the synchronous REST call is the only signal). Required for live
    # delivery tracking; optional in stub/dev mode.
    twilio_status_callback_url: str | None = None
    fcm_service_account_file: str | None = None
    # Slice 16: GCP project id for the FCM HTTP v1 send URL
    # (`/v1/projects/{project_id}/messages:send`). The service account
    # file knows its own project, but FCM requires the project be named in
    # the URL too. Required only when `provider_mode='live'` and FCM is
    # enabled.
    fcm_project_id: str | None = None

    # --- Emergency policy (technical defaults; ops decisions in open-questions) ---
    # No server `acknowledged` within this window -> backup escalation + the
    # mobile fallback action sheet. The countdown never cancels the retrying alert.
    emergency_ack_timeout_seconds: int = 60
    # Claimed notification attempts older than this can be re-queued by the
    # Slice 12 reaper. A fresh claim updates attempted_at, so active provider
    # calls are not immediately recycled.
    notification_stuck_claim_seconds: int = 300
    # The ONLY hardcoded fallback numbers (brief §2.2); everything else is
    # resolved from project settings / the active on-call schedule.
    national_emergency_numbers: str = "108,112"
    # Slice 17 — periodic runner cadence. Each tick calls the two
    # idempotent emergency SLAs: backup escalation (no-ack > 60s) and
    # stuck-claim reaper (provider crashed mid-send). Both service
    # functions are idempotent so over-firing is safe; a tighter tick
    # narrows the worst-case escalation latency. Worst-case backup
    # paging time is `emergency_ack_timeout_seconds + scheduler_tick_seconds`.
    scheduler_tick_seconds: int = 5

    # Slice 15 — CORS allowlist for the Vite dashboard. Comma-separated
    # origins (no trailing slash). Leave blank to disable CORS entirely
    # (no middleware mounted). Local default targets Vite's dev port;
    # production should set this to the deployed dashboard host.
    dashboard_origins: str = "http://localhost:5173"

    @property
    def dashboard_origins_list(self) -> list[str]:
        return [o.strip() for o in self.dashboard_origins.split(",") if o.strip()]

    @property
    def national_emergency_number_list(self) -> list[str]:
        return [n.strip() for n in self.national_emergency_numbers.split(",") if n.strip()]

    # --- Auth / JWT (short-lived access + refresh; RBAC) ---
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 1_209_600

    # OTP login (stub SMS in dev). Codes are short-lived and hashed at rest.
    otp_ttl_seconds: int = 300
    otp_length: int = 6
    otp_max_attempts: int = 5
    otp_request_per_phone_per_5min: int = 3
    otp_request_per_ip_per_hour: int = 30
    otp_verify_per_phone_per_5min: int = 10

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
    # Uploaded medical records are scanned before they reach storage. Keep
    # this separate from notification/storage provider mode so a live provider
    # deploy cannot accidentally imply malware scanning.
    virus_scan_mode: str = "stub"
    clamav_host: str | None = None
    clamav_port: int = 3310

    # --- Hospital handover PDF (Slice 8) ---
    # Mailhog in docker-compose. Live SMTP / SES credentials are picked up
    # when provider_mode='live'; the dev stub never opens a socket.
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = False
    handover_email_from: str = "handover@med-emergency.local"
    handover_email_subject_prefix: str = "Hospital handover"
    # Twilio WhatsApp sender (e.g. 'whatsapp:+14155238886'). Used only when
    # provider_mode='live'; the stub gateway ignores it.
    twilio_whatsapp_from: str | None = None

    def validate_for_runtime(self) -> None:
        """Reject dev JWT secrets outside the zero-keys local/test path."""
        if self.app_env.lower() in {"local", "test"}:
            return
        secret = self.jwt_secret
        if secret == DEFAULT_JWT_SECRET:
            raise SystemExit(
                "JWT_SECRET must be set outside local/test; the development "
                "placeholder is not accepted."
            )
        if len(secret) < 32:
            raise SystemExit(
                "JWT_SECRET must be at least 32 characters outside local/test."
            )
        if len(set(secret)) == 1:
            raise SystemExit(
                "JWT_SECRET must not be a repeated-character value outside local/test."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
