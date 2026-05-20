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


def create_record_url_token(*, storage_key: str, download_name: str, ttl: int) -> str:
    settings = get_settings()
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": storage_key,
        "dn": download_name,
        "type": TokenType.RECORD_URL.value,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_record_url_token(token: str) -> tuple[str, str]:
    """Return (storage_key, download_name); raise TokenError if invalid/expired."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != TokenType.RECORD_URL.value:
        raise TokenError("not a record url token")
    key = payload.get("sub")
    if not key:
        raise TokenError("missing subject")
    return key, payload.get("dn") or "record"


def create_handover_url_token(
    *, handover_id: uuid.UUID, storage_key: str, download_name: str, ttl: int
) -> str:
    """Slice 8: signed-link token bound to a single handover row. The token
    type is kept distinct from ``record_url`` so a leaked record link can never
    be replayed against the handover endpoint, and the audit trail can tell
    the two PHI surfaces apart."""
    settings = get_settings()
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": str(handover_id),
        "sk": storage_key,
        "dn": download_name,
        "type": TokenType.HANDOVER_URL.value,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_handover_url_token(token: str) -> tuple[uuid.UUID, str, str]:
    """Return (handover_id, storage_key, download_name); raise TokenError if
    invalid/expired/wrong-type."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("type") != TokenType.HANDOVER_URL.value:
        raise TokenError("not a handover url token")
    handover_id = payload.get("sub")
    storage_key = payload.get("sk")
    if not handover_id or not storage_key:
        raise TokenError("missing handover binding")
    try:
        return uuid.UUID(handover_id), storage_key, payload.get("dn") or "handover.pdf"
    except ValueError as exc:
        raise TokenError("invalid handover id") from exc
