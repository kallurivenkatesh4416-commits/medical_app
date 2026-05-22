"""FastAPI dependencies: DB session, client IP, current user, RBAC guards."""

import uuid
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from app.config import get_settings
from app.db import engine
from app.enums import NON_PHI_ROLES, Role
from app.models.user import User
from app.security.dashboard_session import (
    dashboard_access_cookie,
    mutating_cookie_request_needs_csrf,
    verify_dashboard_csrf,
)
from app.security.jwt import (
    TokenError,
    decode_access_token,
    decode_registration_token,
)

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def client_ip(request: Request) -> str | None:
    # X-Forwarded-For is client-controlled unless the app is actually behind a
    # trusted proxy. Only honour it when explicitly configured, else the audit
    # log would record attacker-chosen IPs.
    if get_settings().trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_db),
) -> User:
    used_cookie = not (creds and creds.credentials)
    raw_access = (
        creds.credentials if creds and creds.credentials else dashboard_access_cookie(request)
    )
    if not raw_access:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_access_token(raw_access)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    user = session.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status_code=401, detail="Inactive or unknown account")
    if mutating_cookie_request_needs_csrf(request, used_cookie=used_cookie):
        verify_dashboard_csrf(request)
    return user


def get_registration_phone(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Verified phone from a short-lived registration token (onboarding only)."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return decode_registration_token(creds.credentials)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid registration token") from exc


def require_roles(*allowed: Role):
    allowed_values = {r.value for r in allowed}

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_values:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return _dep


def forbid_phi_roles(user: User = Depends(get_current_user)) -> User:
    """Guard for any endpoint that exposes PHI. builder_admin and
    security_desk are blocked unconditionally (brief §2.3 / §13)."""
    if user.role in {r.value for r in NON_PHI_ROLES}:
        raise HTTPException(status_code=403, detail="This role cannot access patient data")
    return user
