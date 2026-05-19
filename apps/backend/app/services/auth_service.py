"""OTP login + JWT issuance + refresh rotation (brief §4, PLAN.md Slice 2).

User provisioning (self-registration) is Slice 3 onboarding; Slice 2 expects
users to exist (seeded). Refresh tokens are opaque, stored hashed, single-use
(rotated on every refresh) with reuse detection.
"""

import uuid
from datetime import timedelta

from sqlmodel import Session, select

from app.config import get_settings
from app.enums import AuditAction
from app.models.auth import OtpCode, RefreshToken
from app.models.base import utcnow
from app.models.user import User
from app.security.hashing import (
    hash_secret,
    new_numeric_otp,
    new_opaque_token,
    verify_secret,
)
from app.security.jwt import create_access_token
from app.services.audit import phone_fingerprint, record_audit
from app.services.notifications import get_notification_gateway


class AuthError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


_now = utcnow


def request_otp(session: Session, *, phone: str, from_ip: str | None) -> str:
    settings = get_settings()
    code = new_numeric_otp(settings.otp_length)

    # Invalidate any still-open codes for this phone (one live code at a time).
    open_codes = session.exec(
        select(OtpCode).where(OtpCode.phone == phone, OtpCode.consumed_at.is_(None))  # type: ignore[union-attr]
    ).all()
    for old in open_codes:
        old.consumed_at = _now()
        session.add(old)

    otp = OtpCode(
        phone=phone,
        code_hash=hash_secret(code),
        expires_at=_now() + timedelta(seconds=settings.otp_ttl_seconds),
    )
    session.add(otp)
    session.commit()

    get_notification_gateway().send_sms(
        to=phone, body=f"Your verification code is {code}"
    )
    record_audit(
        session,
        action=AuditAction.OTP_REQUESTED,
        from_ip=from_ip,
        purpose="auth.otp_request",
        meta={"phone_fp": phone_fingerprint(phone)},
    )
    return code


def _stage_refresh(session: Session, user: User) -> str:
    """Add (but do NOT commit) a new refresh token; return the raw value.
    Caller controls the transaction boundary."""
    settings = get_settings()
    raw_refresh = new_opaque_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_secret(raw_refresh),
            expires_at=_now() + timedelta(seconds=settings.jwt_refresh_ttl_seconds),
        )
    )
    return raw_refresh


def _issue_tokens(session: Session, user: User) -> tuple[str, str]:
    access = create_access_token(
        user_id=user.id, role=user.role, project_id=user.project_id
    )
    raw_refresh = _stage_refresh(session, user)
    session.commit()
    return access, raw_refresh


def consume_otp(
    session: Session, *, phone: str, code: str, from_ip: str | None
) -> User | None:
    """Validate + consume an OTP. Returns the active User, or None when the
    phone is verified but has no account yet (caller offers registration)."""
    otp = session.exec(
        select(OtpCode)
        .where(OtpCode.phone == phone, OtpCode.consumed_at.is_(None))  # type: ignore[union-attr]
        .order_by(OtpCode.created_at.desc())  # type: ignore[union-attr]
    ).first()

    fail_meta = {"phone_fp": phone_fingerprint(phone)}

    if otp is None or otp.expires_at < _now():
        record_audit(
            session,
            action=AuditAction.OTP_VERIFY_FAILED,
            from_ip=from_ip,
            purpose="auth.otp_verify",
            meta={**fail_meta, "reason": "expired_or_missing"},
        )
        raise AuthError(401, "invalid_otp", "Invalid or expired code.")

    otp.attempts += 1
    session.add(otp)
    session.commit()

    if otp.attempts > get_settings().otp_max_attempts:
        otp.consumed_at = _now()
        session.add(otp)
        session.commit()
        record_audit(
            session,
            action=AuditAction.OTP_VERIFY_FAILED,
            from_ip=from_ip,
            purpose="auth.otp_verify",
            meta={**fail_meta, "reason": "too_many_attempts"},
        )
        raise AuthError(429, "too_many_attempts", "Too many attempts. Request a new code.")

    if not verify_secret(code, otp.code_hash):
        record_audit(
            session,
            action=AuditAction.OTP_VERIFY_FAILED,
            from_ip=from_ip,
            purpose="auth.otp_verify",
            meta={**fail_meta, "reason": "mismatch"},
        )
        raise AuthError(401, "invalid_otp", "Invalid or expired code.")

    otp.consumed_at = _now()
    session.add(otp)
    session.commit()

    user = session.exec(
        select(User).where(User.phone == phone, User.deleted_at.is_(None))  # type: ignore[union-attr]
    ).first()
    if user is None or not user.is_active:
        # Verified phone, no account: not a failure — onboarding (Slice 3) will
        # create the resident and audit RESIDENT_REGISTERED.
        return None
    return user


def login_user(
    session: Session, *, user: User, from_ip: str | None
) -> tuple[str, str]:
    # Central guard: no token-issuing path (login, onboarding, idempotent
    # replay) may resurrect a closed/inactive account. data_storage revocation
    # soft-deletes the user, so replay after closure is refused here.
    if not user.is_active or user.deleted_at is not None:
        raise AuthError(403, "account_inactive", "Account is not active.")
    access, refresh = _issue_tokens(session, user)
    record_audit(
        session,
        action=AuditAction.LOGIN_SUCCEEDED,
        actor_user_id=user.id,
        project_id=user.project_id,
        from_ip=from_ip,
        purpose="auth.login",
    )
    return access, refresh


def _revoke_all_user_refresh(
    session: Session, user_id: uuid.UUID, *, commit: bool = True
) -> None:
    tokens = session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    for t in tokens:
        t.revoked_at = _now()
        session.add(t)
    if commit:
        session.commit()
    else:
        # Caller batches this into a larger atomic transaction.
        session.flush()


def rotate_refresh(
    session: Session, *, raw_refresh: str, from_ip: str | None
) -> tuple[str, str]:
    # Serialize concurrent refreshes on the parent token row. On Postgres this
    # is a real row lock (FOR UPDATE); SQLite ignores it (single writer anyway).
    # A losing/replaying request blocks here until the winner commits, so it
    # then observes revoked_at set AND the winner's freshly-inserted token —
    # which it revokes as reuse.
    token = session.exec(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_secret(raw_refresh))
        .with_for_update()
    ).first()

    if token is None:
        raise AuthError(401, "invalid_refresh", "Invalid refresh token.")

    if token.revoked_at is not None:
        # Replay / double-spend: revoke the whole family, including any token a
        # concurrent winner just issued (now visible — we were serialized after
        # it via the row lock).
        _revoke_all_user_refresh(session, token.user_id)
        record_audit(
            session,
            action=AuditAction.REFRESH_REUSE_BLOCKED,
            actor_user_id=token.user_id,
            from_ip=from_ip,
            purpose="auth.refresh",
        )
        raise AuthError(401, "refresh_reuse_detected", "Session revoked. Please log in again.")

    if token.expires_at < _now():
        raise AuthError(401, "invalid_refresh", "Refresh token expired.")

    user = session.get(User, token.user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        raise AuthError(403, "account_inactive", "Account is not active.")

    # One transaction: revoke the old token AND insert its replacement together
    # so a concurrent replay can never see "old revoked, new missing", and the
    # replacement is committed before any loser is unblocked.
    token.revoked_at = _now()
    session.add(token)
    access = create_access_token(
        user_id=user.id, role=user.role, project_id=user.project_id
    )
    new_refresh = _stage_refresh(session, user)
    session.commit()

    record_audit(
        session,
        action=AuditAction.TOKEN_REFRESHED,
        actor_user_id=user.id,
        project_id=user.project_id,
        from_ip=from_ip,
        purpose="auth.refresh",
    )
    return access, new_refresh


def logout(session: Session, *, raw_refresh: str, from_ip: str | None) -> None:
    token = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == hash_secret(raw_refresh))
    ).first()
    if token is not None and token.revoked_at is None:
        token.revoked_at = _now()
        session.add(token)
        session.commit()
        record_audit(
            session,
            action=AuditAction.LOGOUT,
            actor_user_id=token.user_id,
            from_ip=from_ip,
            purpose="auth.logout",
        )
