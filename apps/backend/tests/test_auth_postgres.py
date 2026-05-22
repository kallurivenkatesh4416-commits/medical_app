"""Postgres-only refresh rotation contention coverage."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import Session, select

from app.db import engine
from app.enums import AuditAction, Role
from app.models.audit import AuditLog
from app.models.auth import OtpAttempt, OtpCode, RefreshToken
from app.services import auth_service
from app.services.auth_service import AuthError

pytestmark = pytest.mark.postgres_only


def test_otp_request_phone_limit_serializes_on_postgres(session: Session) -> None:
    phone = "+15554440002"
    barrier = Barrier(4)

    def _request() -> tuple[str, str]:
        barrier.wait()
        with Session(engine) as thread_session:
            try:
                code = auth_service.request_otp(
                    thread_session,
                    phone=phone,
                    from_ip="postgres-otp-race",
                )
                return "ok", code
            except AuthError as exc:
                return "error", exc.code

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: _request(), range(4)))

    assert [kind for kind, _ in outcomes].count("ok") == 3
    assert outcomes.count(("error", "otp_rate_limited")) == 1
    assert len(session.exec(select(OtpAttempt)).all()) == 3
    assert len(session.exec(select(OtpCode)).all()) == 3

    actions = list(session.exec(select(AuditLog.action)).all())
    assert actions.count(AuditAction.OTP_REQUESTED.value) == 3
    assert actions.count(AuditAction.OTP_RATE_LIMITED.value) == 1


def test_refresh_rotation_serializes_reuse_on_postgres(
    make_user,
    login,
    session: Session,
) -> None:
    user = make_user(Role.DOCTOR, phone="+15554440001")
    raw_refresh = login(user.phone)["refresh_token"]
    barrier = Barrier(2)

    def _rotate() -> tuple[str, str]:
        barrier.wait()
        with Session(engine) as thread_session:
            try:
                _, new_refresh = auth_service.rotate_refresh(
                    thread_session,
                    raw_refresh=raw_refresh,
                    from_ip="postgres-race",
                )
                return "ok", new_refresh
            except AuthError as exc:
                return "error", exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: _rotate(), range(2)))

    assert [kind for kind, _ in outcomes].count("ok") == 1
    assert ("error", "refresh_reuse_detected") in outcomes

    session.expire_all()
    tokens = session.exec(
        select(RefreshToken).where(RefreshToken.user_id == user.id)
    ).all()
    assert len(tokens) == 2
    assert all(token.revoked_at is not None for token in tokens)

    actions = list(session.exec(select(AuditLog.action)).all())
    assert actions.count(AuditAction.TOKEN_REFRESHED.value) == 1
    assert actions.count(AuditAction.REFRESH_REUSE_BLOCKED.value) == 1
