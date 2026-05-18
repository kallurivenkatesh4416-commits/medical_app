"""FastAPI application factory.

Slice 1: app bootstrap, structured logging, error envelope, health checks.
Auth/RBAC, audit log, and feature routers are added from Slice 2.
"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.health import router as health_router
from app.config import get_settings
from app.errors import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from app.logging import configure_logging, get_logger


def create_app() -> FastAPI:
    settings = get_settings()
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

    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health_router)

    log.info("app_started", env=settings.app_env, provider_mode=settings.provider_mode)
    return app


app = create_app()
