"""Short-lived JWT access tokens (brief §4). Refresh tokens are opaque and
stored hashed (see app/services/auth_service.py), not JWTs.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.config import get_settings
from app.enums import TokenType


class TokenError(Exception):
    """Invalid, expired, or malformed token."""


def create_access_token(*, user_id: uuid.UUID, role: str, project_id: uuid.UUID | None) -> str:
    settings = get_settings()
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "project_id": str(project_id) if project_id else None,
        "type": TokenType.ACCESS.value,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_access_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != TokenType.ACCESS.value:
        raise TokenError("not an access token")
    return payload


def create_registration_token(*, phone: str) -> str:
    settings = get_settings()
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": phone,
        "type": TokenType.REGISTRATION.value,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(seconds=settings.registration_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_registration_token(token: str) -> str:
    """Return the verified phone, or raise TokenError."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != TokenType.REGISTRATION.value:
        raise TokenError("not a registration token")
    phone = payload.get("sub")
    if not phone:
        raise TokenError("missing subject")
    return phone
