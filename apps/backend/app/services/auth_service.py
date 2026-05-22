"""OTP login + JWT issuance + refresh rotation (brief §4, PLAN.md Slice 2).

User provisioning (self-registration) is Slice 3 onboarding; Slice 2 expects
users to exist (seeded). Refresh tokens are opaque, stored hashed, single-use
(rotated on every refresh) with reuse detection.
"""

import hashlib
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, text
from sqlmodel import Session, delete, select

from app.config import get_settings
from app.enums import AuditAction
from app.models.auth import OtpAttempt, OtpCode, RefreshToken
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

OTP_REQUEST_KIND = "request"
OTP_VERIFY_KIND = "verify"
OTP_PHONE_WINDOW_SECONDS = 300
OTP_IP_REQUEST_WINDOW_SECONDS = 3600
OTP_ATTEMPT_RETENTION_SECONDS = 86_400


def _count_attempts(
    session: Session,
    *,
    kind: str,
    since: datetime,
    phone_fp: str | None = None,
    from_ip: str | None = None,
) -> int:
    statement = select(func.count()).select_from(OtpAttempt).where(
        OtpAttempt.kind == kind,
        OtpAttempt.attempted_at >= since,
    )
    if phone_fp is not None:
        statement = statement.where(OtpAttempt.phone_fp == phone_fp)
    if from_ip is not None:
        statement = statement.where(OtpAttempt.from_ip == from_ip)
    return int(session.exec(statement).one())


def _lock_otp_attempt_scopes(
    session: Session,
    *,
    kind: str,
    phone_fp: str,
    from_ip: str | None,
) -> None:
    """Serialize limiter counts on Postgres before staging a new attempt.

    A sliding-window ``count`` followed by an ``insert`` is otherwise racy
    across backend workers when a scope is just below its limit. SQLite stays
    sequential in local/tests; production uses transaction-scoped advisory
    locks because there may be no existing row to lock for a new phone or IP.
    """
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return

    scopes = {f"otp:{kind}:phone:{phone_fp}"}
    if kind == OTP_REQUEST_KIND and from_ip is not None:
        scopes.add(f"otp:{kind}:ip:{from_ip}")
    for scope in sorted(scopes):
        digest = hashlib.sha256(scope.encode()).digest()
        lock_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )


def _reject_rate_limited(
    session: Session,
    *,
    from_ip: str | None,
    phone_fp: str,
    limit_kind: str,
    window_seconds: int,
    observed_count: int,
) -> None:
    record_audit(
        session,
        action=AuditAction.OTP_RATE_LIMITED,
        from_ip=from_ip,
        purpose="auth.otp_rate_limit",
        meta={
            "phone_fp": phone_fp,
            "limit_kind": limit_kind,
            "window_seconds": window_seconds,
            "observed_count": observed_count,
        },
    )
    raise AuthError(429, "otp_rate_limited", "Too many code attempts. Try again later.")


def _stage_otp_attempt(
    session: Session,
    *,
    phone: str,
    from_ip: str | None,
    kind: str,
) -> None:
    settings = get_settings()
    now = _now()
    phone_fp = phone_fingerprint(phone)
    _lock_otp_attempt_scopes(
        session,
        kind=kind,
        phone_fp=phone_fp,
        from_ip=from_ip,
    )
    phone_count = _count_attempts(
        session,
        kind=kind,
        since=now - timedelta(seconds=OTP_PHONE_WINDOW_SECONDS),
        phone_fp=phone_fp,
    )
    if kind == OTP_REQUEST_KIND:
        if phone_count >= settings.otp_request_per_phone_per_5min:
            _reject_rate_limited(
                session,
                from_ip=from_ip,
                phone_fp=phone_fp,
                limit_kind="phone_request",
                window_seconds=OTP_PHONE_WINDOW_SECONDS,
                observed_count=phone_count,
            )
        if from_ip is not None:
            ip_count = _count_attempts(
                session,
                kind=kind,
                since=now - timedelta(seconds=OTP_IP_REQUEST_WINDOW_SECONDS),
                from_ip=from_ip,
            )
            if ip_count >= settings.otp_request_per_ip_per_hour:
                _reject_rate_limited(
                    session,
                    from_ip=from_ip,
                    phone_fp=phone_fp,
                    limit_kind="ip_request",
                    window_seconds=OTP_IP_REQUEST_WINDOW_SECONDS,
                    observed_count=ip_count,
                )
    elif phone_count >= settings.otp_verify_per_phone_per_5min:
        _reject_rate_limited(
            session,
            from_ip=from_ip,
            phone_fp=phone_fp,
            limit_kind="phone_verify",
            window_seconds=OTP_PHONE_WINDOW_SECONDS,
            observed_count=phone_count,
        )

    session.add(
        OtpAttempt(
            phone_fp=phone_fp,
            from_ip=from_ip,
            kind=kind,
            attempted_at=now,
        )
    )


def purge_old_otp_attempts(
    session: Session,
    *,
    older_than_seconds: int = OTP_ATTEMPT_RETENTION_SECONDS,
    now: datetime | None = None,
) -> int:
    cutoff = (now or _now()) - timedelta(seconds=older_than_seconds)
    result = session.exec(delete(OtpAttempt).where(OtpAttempt.attempted_at < cutoff))
    session.commit()
    return int(result.rowcount or 0)


def request_otp(session: Session, *, phone: str, from_ip: str | None) -> str:
    settings = get_settings()
    _stage_otp_attempt(
        session,
        phone=phone,
        from_ip=from_ip,
        kind=OTP_REQUEST_KIND,
    )
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
    _stage_otp_attempt(
        session,
        phone=phone,
        from_ip=from_ip,
        kind=OTP_VERIFY_KIND,
    )
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
