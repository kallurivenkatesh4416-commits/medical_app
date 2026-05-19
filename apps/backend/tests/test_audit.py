"""Slice 2 — audit log: written on auth events, no raw PII, append-only."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.enums import AuditAction, Role
from app.models import AuditLogImmutableError
from app.models.audit import AuditLog


def test_login_writes_audit_trail(
    client: TestClient, make_user, login, session: Session
) -> None:
    user = make_user(Role.DOCTOR, phone="+15553330001")
    login(user.phone)

    actions = set(session.exec(select(AuditLog.action)).all())
    assert AuditAction.OTP_REQUESTED.value in actions
    assert AuditAction.LOGIN_SUCCEEDED.value in actions

    login_row = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.LOGIN_SUCCEEDED.value)
    ).first()
    assert login_row.actor_user_id == user.id
    assert login_row.from_ip is not None


def test_audit_never_stores_raw_phone(
    client: TestClient, make_user, login, session: Session
) -> None:
    phone = "+15553330002"
    make_user(Role.RESIDENT, phone=phone)
    login(phone)

    for row in session.exec(select(AuditLog)).all():
        serialized = f"{row.resource_id}{row.purpose}{row.meta}"
        assert phone not in serialized


def test_audit_log_is_append_only(session: Session) -> None:
    entry = AuditLog(action="test_action")
    session.add(entry)
    session.commit()

    entry.action = "tampered"
    session.add(entry)
    with pytest.raises(AuditLogImmutableError):
        session.commit()
    session.rollback()

    with pytest.raises(AuditLogImmutableError):
        session.delete(entry)
        session.commit()
    session.rollback()
