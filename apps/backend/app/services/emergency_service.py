"""Emergency service.

Slice 5 created an alerted case and sent one FCM push. Slice 6 hardens it:

- on-call schedule resolution for the primary doctor (+ backup escalation),
- a 3-channel fan-out (FCM push + SMS + voice) where each channel logs to
  ``notification_attempts`` independently and one failing never blocks the
  others or case creation,
- a 60s no-ack backup escalation entry point,
- backend-resolved fallback numbers + a fallback-tap recorder for the mobile
  failed-alert action sheet.

Commit ordering is preserved: the case/event/audit/idempotency rows commit
first; every notification attempt commits a ``queued`` row before the gateway
call and then updates it. A provider crash can never leave an external
notification with no persisted case. ``notification_attempts`` and the stub
gateway never store or log a message body, symptoms, or patient history.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.enums import (
    AuditAction,
    CaseStatus,
    FallbackChannel,
    NotificationChannel,
    NotificationStatus,
)
from app.models.base import utcnow
from app.models.emergency import (
    CaseEvent,
    DeviceToken,
    EmergencyCase,
    NotificationAttempt,
)
from app.models.emergency_contact import EmergencyContact
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User
from app.services import idempotency, on_call
from app.services.audit import record_audit
from app.services.auth_service import AuthError
from app.services.notifications import get_notification_gateway
from app.services.on_call import OnCallResolution
from app.services.residents_service import get_resident_for_user

BACKUP_ESCALATED_EVENT = "backup_escalated"
FALLBACK_INVOKED_EVENT = "fallback_invoked"


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
    primary = on_call.resolve_primary_doctor(session, project_id=resident.project_id)
    case = EmergencyCase(
        project_id=resident.project_id,
        resident_id=resident.id,
        created_by_user_id=user.id,
        assigned_doctor_id=primary.user.id if primary else None,
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

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AuthError(409, "alert_create_conflict", "Emergency alert conflicted.") from exc
    session.refresh(case)

    _fan_out_to_doctor(session, case=case, doctor=primary)
    _maybe_notify_security_desk(session, case=case, resident=resident)
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
        select(NotificationAttempt)
        .where(NotificationAttempt.case_id == case.id)
        .order_by(NotificationAttempt.attempted_at)  # type: ignore[arg-type]
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


# --------------------------------------------------------------------------- #
# Fan-out                                                                      #
# --------------------------------------------------------------------------- #


def _push_token_for(session: Session, user: User) -> DeviceToken | None:
    return session.exec(
        select(DeviceToken)
        .where(
            DeviceToken.user_id == user.id,
            DeviceToken.disabled_at.is_(None),  # type: ignore[union-attr]
        )
        .order_by(DeviceToken.last_seen_at.desc())  # type: ignore[arg-type]
    ).first()


def _attempt(
    session: Session,
    *,
    case: EmergencyCase,
    channel: NotificationChannel,
    recipient_id: uuid.UUID | None,
    send: Callable[[], str] | None,
    skip_error: str | None = None,
) -> None:
    """Record one channel attempt. The ``queued`` row commits before the
    gateway call; the result commits after. A failure here is logged and
    swallowed so it never blocks the case or the other channels."""
    attempt = NotificationAttempt(
        project_id=case.project_id,
        case_id=case.id,
        channel=channel.value,
        recipient_id=recipient_id,
        status=NotificationStatus.QUEUED.value,
        attempted_at=utcnow(),
    )
    if send is None:
        attempt.status = NotificationStatus.FAILED.value
        attempt.error = skip_error
        session.add(attempt)
        session.commit()
        return

    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    try:
        attempt.provider_ref = send()
        attempt.status = NotificationStatus.SENT.value
    except Exception as exc:  # one channel failing must not block the others
        attempt.error = exc.__class__.__name__
        attempt.status = NotificationStatus.FAILED.value
    session.add(attempt)
    session.commit()


def _fan_out_to_doctor(
    session: Session, *, case: EmergencyCase, doctor: OnCallResolution | None
) -> None:
    if doctor is None:
        _attempt(
            session,
            case=case,
            channel=NotificationChannel.FCM,
            recipient_id=None,
            send=None,
            skip_error="no_active_doctor",
        )
        return

    gateway = get_notification_gateway()
    recipient_id = doctor.user.id
    token = _push_token_for(session, doctor.user)
    # PHI-safe body: no symptoms/history over SMS/push. The secure detail lives
    # behind auth in the app feed; the alert only says "open the app".
    title = "Emergency alert"
    body = "A resident needs medical help. Open the Emergency app for details."

    _attempt(
        session,
        case=case,
        channel=NotificationChannel.FCM,
        recipient_id=recipient_id,
        send=(
            (lambda: gateway.send_push(token=token.push_token, title=title, body=body))
            if token is not None
            else None
        ),
        skip_error=None if token is not None else "no_push_token",
    )
    phone = doctor.contact_phone
    _attempt(
        session,
        case=case,
        channel=NotificationChannel.SMS,
        recipient_id=recipient_id,
        send=(lambda: gateway.send_sms(to=phone, body=body)) if phone else None,
        skip_error=None if phone else "no_contact_phone",
    )
    _attempt(
        session,
        case=case,
        channel=NotificationChannel.VOICE,
        recipient_id=recipient_id,
        send=(
            (lambda: gateway.place_voice_call(to=phone, twiml_url=_voice_twiml_url()))
            if phone
            else None
        ),
        skip_error=None if phone else "no_contact_phone",
    )


def _maybe_notify_security_desk(
    session: Session, *, case: EmergencyCase, resident: Resident
) -> None:
    """Opt-in, minimum-necessary only (DPDP / PLAN.md). Security desk gets
    name + flat/villa + location + primary contact phone + case id — never
    symptoms, vitals, history, notes, or records."""
    project = session.get(Project, case.project_id)
    if project is None or not project.enable_security_desk_alerts:
        return
    desk = on_call.resolve_security_desk(session, project_id=case.project_id)
    if desk is None or not desk.contact_phone:
        return
    body = _security_desk_payload(session, case=case, resident=resident)
    gateway = get_notification_gateway()
    _attempt(
        session,
        case=case,
        channel=NotificationChannel.SMS,
        recipient_id=desk.user.id,
        send=lambda: gateway.send_sms(to=desk.contact_phone, body=body),
    )


def _security_desk_payload(
    session: Session, *, case: EmergencyCase, resident: Resident
) -> str:
    user = session.get(User, resident.user_id)
    contact = _primary_contact(session, resident.id)
    fields = [
        user.full_name if user else None,
        f"Flat {resident.flat_villa_number}",
        case.location_text or "location not provided",
        contact.phone if contact else "no primary contact",
        f"case {str(case.id)[:8]}",
    ]
    return " | ".join(str(f) for f in fields)


# --------------------------------------------------------------------------- #
# 60s no-ack backup escalation                                                 #
# --------------------------------------------------------------------------- #


def escalate_stale_alerts(
    session: Session,
    *,
    project_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    from_ip: str | None = None,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Page the backup doctor for ALERTED cases with no acknowledgment within
    ``emergency_ack_timeout_seconds``.

    Lifecycle status transitions are Slice 7 — this only detects "no ack",
    fans out to the backup, and records the escalation. The ``backup_escalated``
    ``case_events`` row is the idempotency marker so a case escalates once even
    if this runs repeatedly. Until background-worker infra lands, an ops user
    triggers this via the escalations endpoint (documented boundary).
    """
    moment = now or utcnow()
    timeout = get_settings().emergency_ack_timeout_seconds
    cutoff = moment - timedelta(seconds=timeout)
    cases = session.exec(
        select(EmergencyCase).where(
            EmergencyCase.project_id == project_id,
            EmergencyCase.status == CaseStatus.ALERTED.value,
            EmergencyCase.acknowledged_at.is_(None),  # type: ignore[union-attr]
            EmergencyCase.alert_time <= cutoff,
        )
    ).all()

    escalated: list[uuid.UUID] = []
    for case in cases:
        already = session.exec(
            select(CaseEvent).where(
                CaseEvent.case_id == case.id,
                CaseEvent.event_type == BACKUP_ESCALATED_EVENT,
            )
        ).first()
        if already is not None:
            continue
        backup = on_call.resolve_backup_doctor(session, project_id=project_id, now=moment)
        session.add(
            CaseEvent(
                project_id=case.project_id,
                case_id=case.id,
                actor_user_id=actor_user_id,
                event_type=BACKUP_ESCALATED_EVENT,
                from_status=case.status,
                to_status=case.status,
                meta={
                    "reason": "no_ack_timeout",
                    "timeout_seconds": timeout,
                    "backup_found": backup is not None,
                },
            )
        )
        record_audit(
            session,
            action=AuditAction.EMERGENCY_ALERT_ESCALATED,
            actor_user_id=actor_user_id,
            project_id=case.project_id,
            resource_type="emergency_case",
            resource_id=str(case.id),
            from_ip=from_ip,
            purpose="emergency.backup_escalation",
            meta={"backup_found": backup is not None},
            commit=False,
        )
        session.commit()
        if backup is not None:
            _fan_out_to_doctor(session, case=case, doctor=backup)
        escalated.append(case.id)
    return escalated


# --------------------------------------------------------------------------- #
# Mobile failed-alert fallback                                                 #
# --------------------------------------------------------------------------- #


def _primary_contact(
    session: Session, resident_id: uuid.UUID
) -> EmergencyContact | None:
    contacts = session.exec(
        select(EmergencyContact)
        .where(EmergencyContact.resident_id == resident_id)
        .order_by(EmergencyContact.priority)  # type: ignore[arg-type]
    ).all()
    for c in contacts:
        if c.is_primary:
            return c
    return contacts[0] if contacts else None


def _voice_twiml_url() -> str:
    # Stub ignores this; the live Twilio webhook lands with the live gateway.
    return f"{get_settings().public_base_url.rstrip('/')}/api/v1/emergency/voice/twiml"


def _owned_case(
    session: Session, *, case_id: uuid.UUID, user: User
) -> tuple[EmergencyCase, Resident]:
    resident = get_resident_for_user(session, user)
    case = session.get(EmergencyCase, case_id)
    if case is None or case.resident_id != resident.id:
        raise AuthError(404, "alert_not_found", "Unknown emergency alert.")
    return case, resident


def fallback_numbers(
    session: Session, *, case_id: uuid.UUID, user: User, from_ip: str | None
) -> dict:
    """Numbers for the mobile fallback sheet — every number is resolved from
    backend state (project / on-call schedule / resident contacts). Only the
    national numbers are constants (brief §2.2)."""
    case, resident = _owned_case(session, case_id=case_id, user=user)
    n108, n112 = get_settings().national_emergency_number_list[:2]

    doctor = on_call.resolve_primary_doctor(session, project_id=case.project_id)
    contact = _primary_contact(session, resident.id)

    project = session.get(Project, case.project_id)
    security_phone: str | None = None
    if project is not None and project.enable_security_desk_alerts:
        desk = on_call.resolve_security_desk(session, project_id=case.project_id)
        security_phone = desk.contact_phone if desk else None

    record_audit(
        session,
        action=AuditAction.EMERGENCY_FALLBACK_NUMBERS_READ,
        actor_user_id=user.id,
        project_id=case.project_id,
        resource_type="emergency_case",
        resource_id=str(case.id),
        from_ip=from_ip,
        purpose="emergency.fallback_numbers",
    )
    return {
        "case_id": case.id,
        "doctor": doctor.contact_phone if doctor else None,
        "emergency_108": n108,
        "emergency_112": n112,
        "family_primary": contact.phone if contact else None,
        "security_desk": security_phone,
    }


def record_fallback(
    session: Session,
    *,
    case_id: uuid.UUID,
    user: User,
    channel: FallbackChannel,
    from_ip: str | None,
) -> dict:
    """Every fallback tap writes a ``case_events`` row with the chosen channel.
    The original alert keeps retrying on the device; this only records intent."""
    case, _ = _owned_case(session, case_id=case_id, user=user)
    session.add(
        CaseEvent(
            project_id=case.project_id,
            case_id=case.id,
            actor_user_id=user.id,
            event_type=FALLBACK_INVOKED_EVENT,
            from_status=case.status,
            to_status=case.status,
            meta={"channel": channel.value},
        )
    )
    record_audit(
        session,
        action=AuditAction.EMERGENCY_FALLBACK_INVOKED,
        actor_user_id=user.id,
        project_id=case.project_id,
        resource_type="emergency_case",
        resource_id=str(case.id),
        from_ip=from_ip,
        purpose="emergency.fallback_invoked",
        meta={"channel": channel.value},
        commit=False,
    )
    session.commit()
    return {"case_id": case.id, "channel": channel.value, "recorded": True}
