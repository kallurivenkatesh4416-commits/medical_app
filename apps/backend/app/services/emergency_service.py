"""Emergency service.

Slice 5 created an alerted case and sent one FCM push. Slice 6 hardens it; the
Slice 6 review further makes notification delivery **durable and resumable**:

- the planned notification attempts (FCM push + SMS + voice to the on-call
  doctor, opt-in security-desk SMS) are staged as ``queued`` rows **inside the
  same transaction as the case/event/audit/idempotency rows**. A crash after
  the commit and before delivery therefore leaves a persisted case *with* its
  queued attempts; an idempotent replay re-runs ``_deliver_pending`` and
  finishes delivery instead of silently dropping it.
- each channel still logs independently and one failing never blocks the
  others or the case,
- a 60s no-ack backup escalation entry point (case-row locked to make the
  check-then-insert race-safe),
- backend-resolved fallback numbers, a resident-owned case-status read, and a
  fallback-tap recorder for the mobile failed-alert sheet.

``notification_attempts`` and the stub gateway never store or log a message
body, symptoms, or patient history.
"""

import uuid
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
    Role,
)
from app.models.base import utcnow
from app.models.emergency import (
    CaseEvent,
    DeviceToken,
    EmergencyCase,
    NotificationAttempt,
)
from app.models.emergency_contact import EmergencyContact
from app.models.on_call import OnCallSchedule
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

_ALERT_TITLE = "Emergency alert"
# PHI-safe: no symptoms/history over SMS/push. Secure detail is behind auth.
_ALERT_BODY = "A resident needs medical help. Open the Emergency app for details."


class _ChannelSkip(Exception):
    """A known, non-exceptional reason a channel could not be sent
    (no push token / no contact phone). The reason is the attempt error."""


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
    # Outbox: queued attempts are durable with the case (same transaction).
    _stage_planned_attempts(session, case=case, doctor=primary)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise AuthError(409, "alert_create_conflict", "Emergency alert conflicted.") from exc
    session.refresh(case)

    _deliver_pending(session, case=case)
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
    # Resume: a retried request finishes any delivery a crash interrupted.
    _deliver_pending(session, case=case)
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


def case_status_for_owner(
    session: Session, *, case_id: uuid.UUID, user: User
) -> dict:
    """Resident-owned, PHI-free status read so the mobile app can tell whether
    its alert was acknowledged (drives the 60s fallback countdown). Owner-only:
    a non-owner gets 404 with no existence leak."""
    case, _ = _owned_case(session, case_id=case_id, user=user)
    return {
        "case_id": case.id,
        "status": case.status,
        "acknowledged": case.acknowledged_at is not None
        or case.status != CaseStatus.ALERTED.value,
    }


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
# Outbox: stage (in the case transaction) then deliver (after commit)          #
# --------------------------------------------------------------------------- #


def _queued(
    case: EmergencyCase,
    channel: NotificationChannel,
    recipient_id: uuid.UUID | None,
) -> NotificationAttempt:
    return NotificationAttempt(
        project_id=case.project_id,
        case_id=case.id,
        channel=channel.value,
        recipient_id=recipient_id,
        status=NotificationStatus.QUEUED.value,
        attempted_at=utcnow(),
    )


def _stage_planned_attempts(
    session: Session, *, case: EmergencyCase, doctor: OnCallResolution | None
) -> None:
    """Add (no commit) the queued rows the caller commits atomically with the
    case. Recipients are resolved fresh at delivery time from recipient_id."""
    if doctor is None:
        session.add(_queued(case, NotificationChannel.FCM, None))
    else:
        for channel in (
            NotificationChannel.FCM,
            NotificationChannel.SMS,
            NotificationChannel.VOICE,
        ):
            session.add(_queued(case, channel, doctor.user.id))

    project = session.get(Project, case.project_id)
    if project is not None and project.enable_security_desk_alerts:
        desk = on_call.resolve_security_desk(session, project_id=case.project_id)
        if desk is not None and desk.contact_phone:
            session.add(_queued(case, NotificationChannel.SMS, desk.user.id))


def _deliver_pending(session: Session, *, case: EmergencyCase) -> None:
    """Send every still-``queued`` attempt for the case. Idempotent: rows that
    already reached sent/failed are skipped, so create + any replay/resume can
    never double-send. One channel failing is logged and swallowed."""
    pending = session.exec(
        select(NotificationAttempt)
        .where(
            NotificationAttempt.case_id == case.id,
            NotificationAttempt.status == NotificationStatus.QUEUED.value,
        )
        .order_by(NotificationAttempt.attempted_at)  # type: ignore[arg-type]
    ).all()
    if not pending:
        return
    gateway = get_notification_gateway()
    resident = session.get(Resident, case.resident_id)
    for attempt in pending:
        _deliver_one(
            session, case=case, attempt=attempt, gateway=gateway, resident=resident
        )


def _deliver_one(
    session: Session,
    *,
    case: EmergencyCase,
    attempt: NotificationAttempt,
    gateway,  # noqa: ANN001 - NotificationGateway Protocol
    resident: Resident | None,
) -> None:
    if attempt.recipient_id is None:
        attempt.status = NotificationStatus.FAILED.value
        attempt.error = "no_active_doctor"
        session.add(attempt)
        session.commit()
        return
    user = session.get(User, attempt.recipient_id)
    if user is None:
        attempt.status = NotificationStatus.FAILED.value
        attempt.error = "recipient_missing"
        session.add(attempt)
        session.commit()
        return

    try:
        if attempt.channel == NotificationChannel.FCM.value:
            token = _push_token_for(session, user)
            if token is None:
                raise _ChannelSkip("no_push_token")
            attempt.provider_ref = gateway.send_push(
                token=token.push_token, title=_ALERT_TITLE, body=_ALERT_BODY
            )
        elif attempt.channel == NotificationChannel.SMS.value:
            phone = _contact_phone_for(session, user)
            if not phone:
                raise _ChannelSkip("no_contact_phone")
            body = (
                _security_desk_payload(session, case=case, resident=resident)
                if user.role == Role.SECURITY_DESK.value
                else _ALERT_BODY
            )
            attempt.provider_ref = gateway.send_sms(to=phone, body=body)
        elif attempt.channel == NotificationChannel.VOICE.value:
            phone = _contact_phone_for(session, user)
            if not phone:
                raise _ChannelSkip("no_contact_phone")
            attempt.provider_ref = gateway.place_voice_call(
                to=phone, twiml_url=_voice_twiml_url()
            )
        else:  # pragma: no cover - guarded by the planner
            raise _ChannelSkip("unknown_channel")
        attempt.status = NotificationStatus.SENT.value
    except _ChannelSkip as skip:
        attempt.status = NotificationStatus.FAILED.value
        attempt.error = str(skip)
    except Exception as exc:  # one channel failing must not block the others
        attempt.status = NotificationStatus.FAILED.value
        attempt.error = exc.__class__.__name__
    session.add(attempt)
    session.commit()


def _push_token_for(session: Session, user: User) -> DeviceToken | None:
    return session.exec(
        select(DeviceToken)
        .where(
            DeviceToken.user_id == user.id,
            DeviceToken.disabled_at.is_(None),  # type: ignore[union-attr]
        )
        .order_by(DeviceToken.last_seen_at.desc())  # type: ignore[arg-type]
    ).first()


def _contact_phone_for(session: Session, user: User) -> str | None:
    """Duty line for SMS/voice — an active on-call ``contact_phone`` for this
    user if one exists, else the user's own phone."""
    now = utcnow()
    row = session.exec(
        select(OnCallSchedule)
        .where(
            OnCallSchedule.user_id == user.id,
            OnCallSchedule.starts_at <= now,
            OnCallSchedule.ends_at > now,
            OnCallSchedule.contact_phone.is_not(None),  # type: ignore[union-attr]
        )
        .order_by(OnCallSchedule.starts_at.desc())  # type: ignore[attr-defined]
    ).first()
    if row is not None and row.contact_phone:
        return row.contact_phone
    return user.phone


def _security_desk_payload(
    session: Session, *, case: EmergencyCase, resident: Resident | None
) -> str:
    """Minimum-necessary only (DPDP / PLAN.md): name + flat/villa + location +
    primary contact phone + case id. Never symptoms/vitals/history."""
    user = session.get(User, resident.user_id) if resident else None
    contact = _primary_contact(session, resident.id) if resident else None
    fields = [
        user.full_name if user else None,
        f"Flat {resident.flat_villa_number}" if resident else "flat unknown",
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

    The candidate cases are selected ``FOR UPDATE`` so two concurrent
    scheduler/ops invocations serialize on the case row: the check-then-insert
    of the ``backup_escalated`` marker cannot both miss. (SQLite ignores the
    row lock; Postgres — production — enforces it. Same pattern as refresh
    rotation, see docs/open-questions.md.) Lifecycle status transitions are
    Slice 7 — this only detects "no ack", fans out to the backup, and records
    the escalation.
    """
    moment = now or utcnow()
    timeout = get_settings().emergency_ack_timeout_seconds
    cutoff = moment - timedelta(seconds=timeout)
    cases = session.exec(
        select(EmergencyCase)
        .where(
            EmergencyCase.project_id == project_id,
            EmergencyCase.status == CaseStatus.ALERTED.value,
            EmergencyCase.acknowledged_at.is_(None),  # type: ignore[union-attr]
            EmergencyCase.alert_time <= cutoff,
        )
        .with_for_update()
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
        if backup is not None:
            for channel in (
                NotificationChannel.FCM,
                NotificationChannel.SMS,
                NotificationChannel.VOICE,
            ):
                session.add(_queued(case, channel, backup.user.id))
        session.commit()
        if backup is not None:
            _deliver_pending(session, case=case)
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
