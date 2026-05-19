"""Emergency happy path service (PLAN.md Slice 5).

Creates an alerted case, records the initial case event, and sends one FCM push
to the current primary doctor. SMS/voice/backup escalation land in Slice 6.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.enums import (
    AuditAction,
    CaseStatus,
    NotificationChannel,
    NotificationStatus,
    Role,
)
from app.models.base import utcnow
from app.models.emergency import (
    CaseEvent,
    DeviceToken,
    EmergencyCase,
    NotificationAttempt,
)
from app.models.resident import Resident
from app.models.user import User
from app.services import idempotency
from app.services.audit import record_audit
from app.services.auth_service import AuthError
from app.services.notifications import get_notification_gateway
from app.services.residents_service import get_resident_for_user


@dataclass
class AlertInput:
    symptom_codes: list[str]
    location_text: str | None
    latitude: float | None
    longitude: float | None
    client_created_at: datetime | None = None


def register_push_token(
    session: Session, *, user: User, token: str, platform: str
) -> DeviceToken:
    token = token.strip()
    platform = platform.strip() or "web"
    if len(token) < 8 or len(token) > 512:
        raise AuthError(422, "invalid_push_token", "Push token is invalid.")
    if len(platform) > 32:
        raise AuthError(422, "invalid_platform", "Platform is too long.")

    row = session.exec(
        select(DeviceToken).where(DeviceToken.push_token == token)
    ).first()
    now = utcnow()
    if row is None:
        row = DeviceToken(
            project_id=user.project_id,
            user_id=user.id,
            platform=platform,
            push_token=token,
            last_seen_at=now,
        )
    else:
        row.project_id = user.project_id
        row.user_id = user.id
        row.platform = platform
        row.disabled_at = None
        row.last_seen_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_alert(
    session: Session,
    *,
    user: User,
    data: AlertInput,
    from_ip: str | None,
    idem_ctx: idempotency.IdemContext | None,
) -> EmergencyCase:
    resident = get_resident_for_user(session, user)
    doctor = _primary_doctor(session, resident.project_id)
    case = EmergencyCase(
        project_id=resident.project_id,
        resident_id=resident.id,
        created_by_user_id=user.id,
        assigned_doctor_id=doctor.id if doctor else None,
        status=CaseStatus.ALERTED.value,
        symptom_codes=data.symptom_codes,
        location_text=data.location_text,
        latitude=data.latitude,
        longitude=data.longitude,
    )
    session.add(case)
    session.flush()

    session.add(
        CaseEvent(
            project_id=case.project_id,
            case_id=case.id,
            actor_user_id=user.id,
            event_type="alert_created",
            from_status=None,
            to_status=CaseStatus.ALERTED.value,
            meta={"symptom_codes": data.symptom_codes},
        )
    )
    record_audit(
        session,
        action=AuditAction.EMERGENCY_ALERT_CREATED,
        actor_user_id=user.id,
        project_id=case.project_id,
        resource_type="emergency_case",
        resource_id=str(case.id),
        from_ip=from_ip,
        purpose="emergency.alert_create",
        meta={"symptom_codes": data.symptom_codes},
        commit=False,
    )
    if idem_ctx is not None:
        idempotency.stage(
            session,
            endpoint=idempotency.EMERGENCY_ALERTS_ENDPOINT,
            ctx=idem_ctx,
            resource_id=case.id,
        )

    _stage_push_attempt(session, case=case, doctor=doctor)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AuthError(409, "alert_create_conflict", "Emergency alert conflicted.") from exc
    session.refresh(case)
    return case


def replay_alert(
    session: Session, ctx: idempotency.IdemContext, *, resident_id: uuid.UUID
) -> EmergencyCase | None:
    try:
        row = idempotency.check_replay(
            session, idempotency.EMERGENCY_ALERTS_ENDPOINT, ctx
        )
    except idempotency.IdempotencyConflict as exc:
        raise AuthError(
            409,
            "idempotency_key_conflict",
            "This Idempotency-Key was used with a different request or account.",
        ) from exc
    if row is None or row.resource_id is None:
        return None
    case = session.get(EmergencyCase, row.resource_id)
    if case is None or case.resident_id != resident_id:
        raise AuthError(404, "alert_not_found", "Unknown emergency alert.")
    return case


def list_active_alerts(session: Session, *, actor: User, from_ip: str | None) -> list[dict]:
    rows = session.exec(
        select(EmergencyCase)
        .where(
            EmergencyCase.project_id == actor.project_id,
            EmergencyCase.status != CaseStatus.CLOSED.value,
        )
        .order_by(EmergencyCase.alert_time.desc())  # type: ignore[arg-type]
    ).all()
    record_audit(
        session,
        action=AuditAction.EMERGENCY_ALERT_LIST,
        actor_user_id=actor.id,
        project_id=actor.project_id,
        resource_type="emergency_case",
        from_ip=from_ip,
        purpose="emergency.alert_feed",
    )
    return [serialize_case(session, case) for case in rows]


def serialize_case(session: Session, case: EmergencyCase) -> dict:
    resident = session.get(Resident, case.resident_id)
    user = session.get(User, resident.user_id) if resident else None
    attempts = session.exec(
        select(NotificationAttempt).where(NotificationAttempt.case_id == case.id)
    ).all()
    return {
        "id": case.id,
        "project_id": case.project_id,
        "resident_id": case.resident_id,
        "resident_name": user.full_name if user else None,
        "flat_villa_number": resident.flat_villa_number if resident else None,
        "status": case.status,
        "alert_time": case.alert_time,
        "symptom_codes": case.symptom_codes,
        "location_text": case.location_text,
        "assigned_doctor_id": case.assigned_doctor_id,
        "notification_attempts": [
            {
                "channel": a.channel,
                "recipient_id": a.recipient_id,
                "status": a.status,
                "provider_ref": a.provider_ref,
                "error": a.error,
            }
            for a in attempts
        ],
    }


def _primary_doctor(session: Session, project_id: uuid.UUID) -> User | None:
    return session.exec(
        select(User)
        .where(
            User.project_id == project_id,
            User.role == Role.DOCTOR.value,
            User.is_active.is_(True),  # type: ignore[union-attr]
            User.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        .order_by(User.created_at)  # type: ignore[arg-type]
    ).first()


def _push_token_for(session: Session, doctor: User) -> DeviceToken | None:
    return session.exec(
        select(DeviceToken)
        .where(
            DeviceToken.user_id == doctor.id,
            DeviceToken.disabled_at.is_(None),  # type: ignore[union-attr]
        )
        .order_by(DeviceToken.last_seen_at.desc())  # type: ignore[arg-type]
    ).first()


def _stage_push_attempt(
    session: Session, *, case: EmergencyCase, doctor: User | None
) -> None:
    attempt = NotificationAttempt(
        project_id=case.project_id,
        case_id=case.id,
        channel=NotificationChannel.FCM.value,
        recipient_id=doctor.id if doctor else None,
        status=NotificationStatus.FAILED.value,
        attempted_at=utcnow(),
    )
    if doctor is None:
        attempt.error = "no_active_doctor"
        session.add(attempt)
        return

    token = _push_token_for(session, doctor)
    if token is None:
        attempt.error = "no_push_token"
        session.add(attempt)
        return

    try:
        attempt.provider_ref = get_notification_gateway().send_push(
            token=token.push_token,
            title="Emergency alert",
            body="A resident needs medical help.",
        )
        attempt.status = NotificationStatus.SENT.value
    except Exception as exc:  # pragma: no cover - defensive around live gateways
        attempt.error = exc.__class__.__name__
        attempt.status = NotificationStatus.FAILED.value
    session.add(attempt)
