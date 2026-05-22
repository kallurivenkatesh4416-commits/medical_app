"""Dashboard cookie-session and CSRF helpers.

Mobile keeps the bearer-token transport. Staff dashboard sessions use the same
JWT/refresh rotation primitives but move both tokens into host-only HttpOnly
cookies and bind write requests to a signed per-session CSRF token.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, Request, Response

from app.config import get_settings
from app.security.hashing import new_opaque_token

DASHBOARD_ACCESS_COOKIE = "med_dashboard_access"
DASHBOARD_REFRESH_COOKIE = "med_dashboard_refresh"
DASHBOARD_SESSION_COOKIE = "med_dashboard_session"
DASHBOARD_CSRF_HEADER = "X-CSRF-Token"

ACCESS_COOKIE_PATH = "/api/v1"
REFRESH_COOKIE_PATH = "/api/v1/auth"
SESSION_COOKIE_PATH = "/api/v1"

_CSRF_TYPE = "dashboard_csrf"


def set_dashboard_session_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    session_id: str | None = None,
) -> str:
    """Write cookies for a dashboard session and return its stable session id."""
    settings = get_settings()
    cookie_session_id = session_id or new_opaque_token()
    response.set_cookie(
        DASHBOARD_ACCESS_COOKIE,
        access_token,
        httponly=True,
        secure=_secure_cookies(),
        samesite="strict",
        max_age=settings.jwt_access_ttl_seconds,
        path=ACCESS_COOKIE_PATH,
    )
    response.set_cookie(
        DASHBOARD_REFRESH_COOKIE,
        refresh_token,
        httponly=True,
        secure=_secure_cookies(),
        samesite="strict",
        max_age=settings.jwt_refresh_ttl_seconds,
        path=REFRESH_COOKIE_PATH,
    )
    response.set_cookie(
        DASHBOARD_SESSION_COOKIE,
        cookie_session_id,
        httponly=True,
        secure=_secure_cookies(),
        samesite="strict",
        max_age=settings.jwt_refresh_ttl_seconds,
        path=SESSION_COOKIE_PATH,
    )
    return cookie_session_id


def clear_dashboard_session_cookies(response: Response) -> None:
    response.delete_cookie(DASHBOARD_ACCESS_COOKIE, path=ACCESS_COOKIE_PATH)
    response.delete_cookie(DASHBOARD_REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)
    response.delete_cookie(DASHBOARD_SESSION_COOKIE, path=SESSION_COOKIE_PATH)


def dashboard_refresh_cookie(request: Request) -> str | None:
    return request.cookies.get(DASHBOARD_REFRESH_COOKIE)


def dashboard_access_cookie(request: Request) -> str | None:
    return request.cookies.get(DASHBOARD_ACCESS_COOKIE)


def dashboard_session_cookie(request: Request) -> str | None:
    return request.cookies.get(DASHBOARD_SESSION_COOKIE)


def csrf_cookie_session_present(request: Request) -> bool:
    return bool(
        dashboard_session_cookie(request)
        and (dashboard_access_cookie(request) or dashboard_refresh_cookie(request))
    )


def create_dashboard_csrf_token(*, session_id: str) -> str:
    settings = get_settings()
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": session_id,
        "type": _CSRF_TYPE,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_dashboard_csrf(request: Request) -> None:
    session_id = dashboard_session_cookie(request)
    token = request.headers.get(DASHBOARD_CSRF_HEADER)
    if not session_id or not token:
        raise _csrf_rejected()
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[get_settings().jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise _csrf_rejected() from exc
    if payload.get("type") != _CSRF_TYPE:
        raise _csrf_rejected()
    token_session_id = str(payload.get("sub") or "")
    if not hmac.compare_digest(token_session_id, session_id):
        raise _csrf_rejected()


def mutating_cookie_request_needs_csrf(request: Request, *, used_cookie: bool) -> bool:
    return used_cookie and request.method.upper() in {"POST", "PATCH", "DELETE"}


def _secure_cookies() -> bool:
    return get_settings().app_env.lower() not in {"local", "test"}


def _csrf_rejected() -> HTTPException:
    return HTTPException(status_code=403, detail="CSRF token missing or invalid")
