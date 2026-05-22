"""FastAPI application factory.

Slice 1: app bootstrap, structured logging, error envelope, health checks.
Slice 2: OTP auth, JWT, RBAC, append-only audit log.
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.emergency import router as emergency_router
from app.api.handover import router as handover_router
from app.api.health import router as health_router
from app.api.medicines import router as medicines_router
from app.api.notifications import router as notifications_router
from app.api.onboarding import router as onboarding_router
from app.api.profile import router as profile_router
from app.api.records import router as records_router
from app.config import get_settings
from app.errors import (
    auth_exception_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.logging import configure_logging, get_logger
from app.services.auth_service import AuthError


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_for_runtime()
    configure_logging(settings.log_level)
    log = get_logger("app")

    app = FastAPI(
        title="Residential Emergency Health Response Platform",
        version=__version__,
        # No diagnosis language anywhere (brief §2.1 / §13).
        description=(
            "Emergency alerting and care coordination API. This app does not "
            "replace emergency hospital care."
        ),
    )

    # Slice 18b — cookie dashboard auth needs exact-origin credentialed CORS.
    # Leave DASHBOARD_ORIGINS blank to mount no CORS middleware at all.
    origins = settings.dashboard_origins_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-CSRF-Token",
            ],
            allow_credentials=True,
        )

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(AuthError, auth_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(onboarding_router)
    app.include_router(profile_router)
    app.include_router(records_router)
    app.include_router(emergency_router)
    app.include_router(handover_router)
    app.include_router(medicines_router)
    app.include_router(admin_router)
    app.include_router(notifications_router)

    log.info("app_started", env=settings.app_env, provider_mode=settings.provider_mode)
    return app


app = create_app()
