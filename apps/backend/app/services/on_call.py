"""On-call resolution (PLAN.md Slice 6).

Fan-out resolves the *active primary* recipient for a role from
``on_call_schedules``; the 60s no-ack timer escalates to the *active backup*.

Conservative fallback: if a project has no doctor schedule rows at all, primary
doctor resolution falls back to the Slice 5 behaviour (first active doctor by
``created_at``) so an emergency alert is never silently left with no doctor.
A *backup* doctor has no such fallback — escalation requires an explicit
``is_backup=true`` schedule, otherwise there is simply nobody extra to page.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.enums import Role
from app.models.base import utcnow
from app.models.on_call import OnCallSchedule
from app.models.user import User


@dataclass
class OnCallResolution:
    user: User
    #: Duty line for SMS/voice — schedule.contact_phone, else the user's phone.
    contact_phone: str | None
    #: "schedule" (an on_call_schedules row) or "fallback" (Slice 5 behaviour).
    source: str


def _eligible_user(
    session: Session,
    user_id: uuid.UUID,
    *,
    project_id: uuid.UUID,
    role: Role,
) -> User | None:
    """Active **and** still in the scheduled project with the scheduled role.
    Bad/stale schedule data must never page the wrong tenant or role."""
    user = session.get(User, user_id)
    if user is None or not user.is_active or user.deleted_at is not None:
        return None
    if user.project_id != project_id or user.role != role.value:
        return None
    return user


def resolve_on_call(
    session: Session,
    *,
    project_id: uuid.UUID,
    role: Role,
    backup: bool = False,
    now: datetime | None = None,
) -> OnCallResolution | None:
    """Active scheduled recipient for ``role`` in ``project_id``, or None.

    Active = ``starts_at <= now < ends_at`` and ``is_backup`` matches. The most
    recently started covering shift wins when several overlap.
    """
    moment = now or utcnow()
    rows = session.exec(
        select(OnCallSchedule)
        .where(
            OnCallSchedule.project_id == project_id,
            OnCallSchedule.role == role.value,
            OnCallSchedule.is_backup == backup,
            OnCallSchedule.starts_at <= moment,
            OnCallSchedule.ends_at > moment,
        )
        .order_by(OnCallSchedule.starts_at.desc())  # type: ignore[attr-defined]
    ).all()
    for row in rows:
        user = _eligible_user(
            session, row.user_id, project_id=project_id, role=role
        )
        if user is not None:
            return OnCallResolution(
                user=user,
                contact_phone=row.contact_phone or user.phone,
                source="schedule",
            )
    return None


def resolve_primary_doctor(
    session: Session, *, project_id: uuid.UUID, now: datetime | None = None
) -> OnCallResolution | None:
    scheduled = resolve_on_call(
        session, project_id=project_id, role=Role.DOCTOR, backup=False, now=now
    )
    if scheduled is not None:
        return scheduled
    # Conservative fallback (documented): never leave an alert doctor-less.
    doctor = session.exec(
        select(User)
        .where(
            User.project_id == project_id,
            User.role == Role.DOCTOR.value,
            User.is_active.is_(True),  # type: ignore[union-attr]
            User.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        .order_by(User.created_at)  # type: ignore[arg-type]
    ).first()
    if doctor is None:
        return None
    return OnCallResolution(user=doctor, contact_phone=doctor.phone, source="fallback")


def resolve_backup_doctor(
    session: Session, *, project_id: uuid.UUID, now: datetime | None = None
) -> OnCallResolution | None:
    return resolve_on_call(
        session, project_id=project_id, role=Role.DOCTOR, backup=True, now=now
    )


def resolve_security_desk(
    session: Session, *, project_id: uuid.UUID, now: datetime | None = None
) -> OnCallResolution | None:
    return resolve_on_call(
        session, project_id=project_id, role=Role.SECURITY_DESK, backup=False, now=now
    )
