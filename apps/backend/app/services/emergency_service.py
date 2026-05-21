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

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.enums import (
    AuditAction,
    CaseNoteType,
    CaseStatus,
    FallbackChannel,
    NotificationChannel,
    NotificationStatus,
    Role,
)
from app.models.base import utcnow
from app.models.emergency import (
    CaseEvent,
    CaseNote,
    CaseVital,
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
VITALS_RECORDED_EVENT = "vitals_recorded"
CASE_NOTE_RECORDED_EVENT = "case_note_recorded"

_ALERT_TITLE = "Emergency alert"
# PHI-safe: no symptoms/history over SMS/push. Secure detail is behind auth.
_ALERT_BODY = "A resident needs medical help. Open the Emergency app for details."
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    CaseStatus.ALERTED.value: {CaseStatus.ACKNOWLEDGED.value},
    CaseStatus.ACKNOWLEDGED.value: {CaseStatus.EN_ROUTE.value},
    CaseStatus.EN_ROUTE.value: {CaseStatus.ON_SITE.value},
    CaseStatus.ON_SITE.value: {
        CaseStatus.TREATED_ON_SITE.value,
        CaseStatus.ESCALATED.value,
    },
    CaseStatus.TREATED_ON_SITE.value: {CaseStatus.CLOSED.value},
    CaseStatus.ESCALATED.value: {CaseStatus.CLOSED.value},
}
_DOCTOR_ONLY_TRANSITIONS = {
    CaseStatus.TREATED_ON_SITE.value,
    CaseStatus.ESCALATED.value,
    CaseStatus.CLOSED.value,
}


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


@dataclass
class VitalInput:
    blood_pressure_systolic: int | None
    blood_pressure_diastolic: int | None
    spo2_percent: int | None
    heart_rate_bpm: int | None
    respiratory_rate_bpm: int | None
    temperature_c: float | None
    notes: str | None = None


@dataclass
class NoteInput:
    note_type: CaseNoteType
    body: str
    doctor_name: str | None
    doctor_registration_number: str | None
    consultation_timestamp: datetime | None
    advice_given: str | None
    patient_consent_obtained: bool


def register_push_token(
    session: Session,
    *,
    user: User,
    token: str,
    platform: str,
    from_ip: str | None = None,
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
    # Flush first so the row id is populated before audit (the create-alert
    # pattern from this file is the same: add -> flush -> derived rows).
    session.flush()
    # Slice 16: every registration is audited so a future device-rotation
    # incident has a trail (who registered which platform when, never the
    # token value — that's the same PHI-discipline as the Slice 6
    # notification_attempts rows).
    record_audit(
        session,
        action=AuditAction.DEVICE_TOKEN_REGISTERED,
        actor_user_id=user.id,
        project_id=user.project_id,
        resource_type="device_token",
        resource_id=str(row.id),
        from_ip=from_ip,
        purpose="emergency.device_token_register",
        meta={"platform": platform},
        commit=False,
    )
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


def read_case_detail(
    session: Session, *, actor: User, case_id: uuid.UUID, from_ip: str | None
) -> dict:
    case = _case_for_staff(session, actor=actor, case_id=case_id)
    record_audit(
        session,
        action=AuditAction.EMERGENCY_CASE_READ,
        actor_user_id=actor.id,
        project_id=case.project_id,
        resource_type="emergency_case",
        resource_id=str(case.id),
        from_ip=from_ip,
        purpose="emergency.case_detail",
    )
    return serialize_case_detail(session, case)


def transition_case(
    session: Session,
    *,
    actor: User,
    case_id: uuid.UUID,
    target_status: CaseStatus,
    resolved_outcome: str | None,
    from_ip: str | None,
) -> dict:
    """Move a case through the strict Slice 7 lifecycle. The case row is locked
    only for this short transaction so it cooperates with Slice 6 escalation's
    ``FOR UPDATE`` candidate selection."""
    case = _case_for_staff(session, actor=actor, case_id=case_id, lock=True)
    current = case.status
    target = target_status.value

    if target == current:
        return serialize_case(session, case)
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise AuthError(
            409,
            "invalid_case_transition",
            f"Case cannot move from {current} to {target}.",
        )
    if target in _DOCTOR_ONLY_TRANSITIONS and actor.role != Role.DOCTOR.value:
        raise AuthError(403, "doctor_required", "Only a doctor can record this outcome.")

    now = utcnow()
    if target == CaseStatus.ACKNOWLEDGED.value:
        case.acknowledged_at = case.acknowledged_at or now
        if actor.role == Role.DOCTOR.value and case.assigned_doctor_id is None:
            case.assigned_doctor_id = actor.id
        _mark_actor_attempts_acknowledged(session, case_id=case.id, actor_id=actor.id)
    elif target == CaseStatus.EN_ROUTE.value:
        case.en_route_at = case.en_route_at or now
    elif target == CaseStatus.ON_SITE.value:
        case.on_site_at = case.on_site_at or now
    elif target == CaseStatus.TREATED_ON_SITE.value:
        case.resolved_outcome = resolved_outcome or case.resolved_outcome or target
    elif target == CaseStatus.ESCALATED.value:
        case.escalated_at = case.escalated_at or now
        case.resolved_outcome = resolved_outcome or case.resolved_outcome or target
    elif target == CaseStatus.CLOSED.value:
        case.closed_at = case.closed_at or now
        if resolved_outcome:
            case.resolved_outcome = resolved_outcome

    case.status = target
    session.add(case)
    session.add(
        CaseEvent(
            project_id=case.project_id,
            case_id=case.id,
            actor_user_id=actor.id,
            event_type=f"case_{target}",
            from_status=current,
            to_status=target,
            meta={"resolved_outcome": resolved_outcome} if resolved_outcome else {},
        )
    )
    record_audit(
        session,
        action=AuditAction.EMERGENCY_CASE_TRANSITIONED,
        actor_user_id=actor.id,
        project_id=case.project_id,
        resource_type="emergency_case",
        resource_id=str(case.id),
        from_ip=from_ip,
        purpose="emergency.case_transition",
        meta={"from_status": current, "to_status": target},
        commit=False,
    )
    session.commit()
    session.refresh(case)
    return serialize_case(session, case)


def record_vitals(
    session: Session,
    *,
    actor: User,
    case_id: uuid.UUID,
    data: VitalInput,
    from_ip: str | None,
) -> dict:
    case = _case_for_staff(session, actor=actor, case_id=case_id)
    if case.status not in {
        CaseStatus.ON_SITE.value,
        CaseStatus.TREATED_ON_SITE.value,
        CaseStatus.ESCALATED.value,
    }:
        raise AuthError(
            409,
            "case_not_on_site",
            "Vitals can be recorded after the case is marked on-site.",
        )
    if all(
        value is None
        for value in (
            data.blood_pressure_systolic,
            data.blood_pressure_diastolic,
            data.spo2_percent,
            data.heart_rate_bpm,
            data.respiratory_rate_bpm,
            data.temperature_c,
        )
    ):
        raise AuthError(422, "vitals_required", "At least one vital reading is required.")

    vital = CaseVital(
        project_id=case.project_id,
        case_id=case.id,
        recorded_by=actor.id,
        blood_pressure_systolic=data.blood_pressure_systolic,
        blood_pressure_diastolic=data.blood_pressure_diastolic,
        spo2_percent=data.spo2_percent,
        heart_rate_bpm=data.heart_rate_bpm,
        respiratory_rate_bpm=data.respiratory_rate_bpm,
        temperature_c=data.temperature_c,
        notes=data.notes,
    )
    session.add(vital)
    session.flush()
    session.add(
        CaseEvent(
            project_id=case.project_id,
            case_id=case.id,
            actor_user_id=actor.id,
            event_type=VITALS_RECORDED_EVENT,
            from_status=case.status,
            to_status=case.status,
            meta={"vital_id": str(vital.id)},
        )
    )
    record_audit(
        session,
        action=AuditAction.CASE_VITAL_RECORDED,
        actor_user_id=actor.id,
        project_id=case.project_id,
        resource_type="case_vital",
        resource_id=str(vital.id),
        from_ip=from_ip,
        purpose="emergency.vitals",
        commit=False,
    )
    session.commit()
    session.refresh(vital)
    return serialize_vital(vital)


def record_case_note(
    session: Session,
    *,
    actor: User,
    case_id: uuid.UUID,
    data: NoteInput,
    from_ip: str | None,
) -> dict:
    case = _case_for_staff(session, actor=actor, case_id=case_id)
    if case.status == CaseStatus.CLOSED.value:
        raise AuthError(409, "case_closed", "Closed cases cannot receive new notes.")
    if data.note_type in {
        CaseNoteType.TREATMENT,
        CaseNoteType.ESCALATION_REASON,
    } and actor.role != Role.DOCTOR.value:
        raise AuthError(403, "doctor_required", "Only a doctor can record this note.")
    if data.note_type == CaseNoteType.TREATMENT and any(
        not value
        for value in (
            data.doctor_name,
            data.doctor_registration_number,
            data.consultation_timestamp,
            data.advice_given,
        )
    ):
        raise AuthError(
            422,
            "telemedicine_fields_required",
            "Doctor name, registration number, consultation timestamp, and advice are required.",
        )

    note = CaseNote(
        project_id=case.project_id,
        case_id=case.id,
        author_id=actor.id,
        note_type=data.note_type.value,
        body=data.body,
        doctor_name=data.doctor_name,
        doctor_registration_number=data.doctor_registration_number,
        consultation_timestamp=data.consultation_timestamp,
        advice_given=data.advice_given,
        patient_consent_obtained=data.patient_consent_obtained,
    )
    session.add(note)
    session.flush()
    session.add(
        CaseEvent(
            project_id=case.project_id,
            case_id=case.id,
            actor_user_id=actor.id,
            event_type=CASE_NOTE_RECORDED_EVENT,
            from_status=case.status,
            to_status=case.status,
            meta={"note_id": str(note.id), "note_type": note.note_type},
        )
    )
    record_audit(
        session,
        action=AuditAction.CASE_NOTE_RECORDED,
        actor_user_id=actor.id,
        project_id=case.project_id,
        resource_type="case_note",
        resource_id=str(note.id),
        from_ip=from_ip,
        purpose="emergency.case_note",
        meta={"note_type": note.note_type},
        commit=False,
    )
    session.commit()
    session.refresh(note)
    return serialize_note(note)


def emergency_kpis(session: Session, *, actor: User, from_ip: str | None) -> dict:
    if actor.project_id is None:
        raise AuthError(403, "project_required", "A project-scoped account is required.")
    cases = session.exec(
        select(EmergencyCase).where(EmergencyCase.project_id == actor.project_id)
    ).all()
    ack_seconds = [
        (case.acknowledged_at - case.alert_time).total_seconds()
        for case in cases
        if case.acknowledged_at is not None
    ]
    on_site_seconds = [
        (case.on_site_at - case.alert_time).total_seconds()
        for case in cases
        if case.on_site_at is not None
    ]
    record_audit(
        session,
        action=AuditAction.EMERGENCY_KPI_READ,
        actor_user_id=actor.id,
        project_id=actor.project_id,
        resource_type="emergency_case",
        from_ip=from_ip,
        purpose="emergency.kpis",
    )
    return {
        "project_id": actor.project_id,
        "total_cases": len(cases),
        "active_cases": sum(1 for c in cases if c.status != CaseStatus.CLOSED.value),
        "closed_cases": sum(1 for c in cases if c.status == CaseStatus.CLOSED.value),
        "average_ack_seconds": _avg(ack_seconds),
        "average_on_site_seconds": _avg(on_site_seconds),
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
        "acknowledged_at": case.acknowledged_at,
        "en_route_at": case.en_route_at,
        "on_site_at": case.on_site_at,
        "escalated_at": case.escalated_at,
        "closed_at": case.closed_at,
        "symptom_codes": case.symptom_codes,
        "location_text": case.location_text,
        "assigned_doctor_id": case.assigned_doctor_id,
        "resolved_outcome": case.resolved_outcome,
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


def serialize_case_detail(session: Session, case: EmergencyCase) -> dict:
    vitals = session.exec(
        select(CaseVital)
        .where(CaseVital.case_id == case.id)
        .order_by(CaseVital.recorded_at.desc())  # type: ignore[arg-type]
    ).all()
    notes = session.exec(
        select(CaseNote)
        .where(CaseNote.case_id == case.id)
        .order_by(CaseNote.created_at.desc())  # type: ignore[arg-type]
    ).all()
    events = session.exec(
        select(CaseEvent)
        .where(CaseEvent.case_id == case.id)
        .order_by(CaseEvent.created_at)  # type: ignore[arg-type]
    ).all()
    return {
        **serialize_case(session, case),
        "vitals": [serialize_vital(v) for v in vitals],
        "notes": [serialize_note(n) for n in notes],
        "events": [serialize_event(e) for e in events],
    }


def serialize_vital(vital: CaseVital) -> dict:
    return {
        "id": vital.id,
        "case_id": vital.case_id,
        "project_id": vital.project_id,
        "recorded_by": vital.recorded_by,
        "recorded_at": vital.recorded_at,
        "blood_pressure_systolic": vital.blood_pressure_systolic,
        "blood_pressure_diastolic": vital.blood_pressure_diastolic,
        "spo2_percent": vital.spo2_percent,
        "heart_rate_bpm": vital.heart_rate_bpm,
        "respiratory_rate_bpm": vital.respiratory_rate_bpm,
        "temperature_c": vital.temperature_c,
        "notes": vital.notes,
    }


def serialize_note(note: CaseNote) -> dict:
    return {
        "id": note.id,
        "case_id": note.case_id,
        "project_id": note.project_id,
        "author_id": note.author_id,
        "note_type": note.note_type,
        "body": note.body,
        "doctor_name": note.doctor_name,
        "doctor_registration_number": note.doctor_registration_number,
        "consultation_timestamp": note.consultation_timestamp,
        "advice_given": note.advice_given,
        "patient_consent_obtained": note.patient_consent_obtained,
        "created_at": note.created_at,
    }


def serialize_event(event: CaseEvent) -> dict:
    return {
        "id": event.id,
        "case_id": event.case_id,
        "actor_user_id": event.actor_user_id,
        "event_type": event.event_type,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "meta": event.meta,
        "created_at": event.created_at,
    }


def _case_for_staff(
    session: Session, *, actor: User, case_id: uuid.UUID, lock: bool = False
) -> EmergencyCase:
    stmt = select(EmergencyCase).where(
        EmergencyCase.id == case_id,
        EmergencyCase.project_id == actor.project_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    case = session.exec(stmt).first()
    if case is None:
        raise AuthError(404, "alert_not_found", "Unknown emergency alert.")
    return case


def _mark_actor_attempts_acknowledged(
    session: Session, *, case_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    attempts = session.exec(
        select(NotificationAttempt).where(
            NotificationAttempt.case_id == case_id,
            NotificationAttempt.recipient_id == actor_id,
            NotificationAttempt.status.in_(  # type: ignore[attr-defined]
                [NotificationStatus.SENT.value, NotificationStatus.DELIVERED.value]
            ),
        )
    ).all()
    for attempt in attempts:
        attempt.status = NotificationStatus.ACKNOWLEDGED.value
        session.add(attempt)


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


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


def requeue_stuck_notification_attempts(
    session: Session,
    *,
    project_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    from_ip: str | None = None,
    older_than_seconds: int | None = None,
    now: datetime | None = None,
    deliver: bool = True,
) -> list[uuid.UUID]:
    """Recover provider-crash rows left in ``sending``.

    Delivery first claims ``queued -> sending`` so concurrent create/replay
    callers cannot double-page. If the process dies after that claim and before
    writing ``sent``/``failed``, the only safe recovery is an age-gated requeue.
    This may resend one old attempt, but it never races with an active provider
    call because fresh claims update ``attempted_at``.
    """
    moment = now or utcnow()
    threshold = max(
        1,
        older_than_seconds
        if older_than_seconds is not None
        else get_settings().notification_stuck_claim_seconds,
    )
    cutoff = moment - timedelta(seconds=threshold)
    stuck = session.exec(
        select(NotificationAttempt)
        .where(
            NotificationAttempt.project_id == project_id,
            NotificationAttempt.status == NotificationStatus.SENDING.value,
            NotificationAttempt.attempted_at <= cutoff,
        )
        .order_by(NotificationAttempt.attempted_at)  # type: ignore[arg-type]
    ).all()
    if not stuck:
        return []

    attempt_ids = [a.id for a in stuck]
    case_ids = sorted({a.case_id for a in stuck}, key=str)
    for attempt in stuck:
        attempt.status = NotificationStatus.QUEUED.value
        attempt.error = "requeued_after_stuck_claim"
        attempt.attempted_at = moment
        session.add(attempt)
    record_audit(
        session,
        action=AuditAction.EMERGENCY_NOTIFICATION_REQUEUED,
        actor_user_id=actor_user_id,
        project_id=project_id,
        resource_type="notification_attempt",
        from_ip=from_ip,
        purpose="emergency.notification_reaper",
        meta={
            "attempt_ids": [str(i) for i in attempt_ids],
            "older_than_seconds": threshold,
        },
        commit=False,
    )
    session.commit()

    if deliver:
        for case_id in case_ids:
            case = session.get(EmergencyCase, case_id)
            if case is not None:
                _deliver_pending(session, case=case)
    return attempt_ids


def _claim_attempt(session: Session, attempt: NotificationAttempt) -> bool:
    """Atomically move this attempt ``queued -> sending``. Returns True iff
    THIS caller won the claim. A conditional UPDATE (only matches while still
    ``queued``) makes the original delivery and a concurrent replay/resume
    mutually exclusive, so the provider is called at most once per attempt.

    The provider crash window (claimed but never finalised) leaves a row in
    ``sending`` until the Slice 12 reaper age-gates and redelivers it."""
    result = session.execute(
        update(NotificationAttempt)
        .where(
            NotificationAttempt.id == attempt.id,
            NotificationAttempt.status == NotificationStatus.QUEUED.value,
        )
        .values(status=NotificationStatus.SENDING.value, attempted_at=utcnow())
    )
    session.commit()
    claimed = result.rowcount == 1
    if claimed:
        session.refresh(attempt)
    return claimed


def _deliver_one(
    session: Session,
    *,
    case: EmergencyCase,
    attempt: NotificationAttempt,
    gateway,  # noqa: ANN001 - NotificationGateway Protocol
    resident: Resident | None,
) -> None:
    # Concurrency-safe: only the winner of the atomic claim sends.
    if not _claim_attempt(session, attempt):
        return
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
            phone = _contact_phone_for(session, user, project_id=case.project_id)
            if not phone:
                raise _ChannelSkip("no_contact_phone")
            body = (
                _security_desk_payload(session, case=case, resident=resident)
                if user.role == Role.SECURITY_DESK.value
                else _ALERT_BODY
            )
            attempt.provider_ref = gateway.send_sms(to=phone, body=body)
        elif attempt.channel == NotificationChannel.VOICE.value:
            phone = _contact_phone_for(session, user, project_id=case.project_id)
            if not phone:
                raise _ChannelSkip("no_contact_phone")
            attempt.provider_ref = gateway.place_voice_call(
                to=phone, twiml_url=_voice_twiml_url()
            )
        else:  # pragma: no cover - guarded by the planner
            raise _ChannelSkip("unknown_channel")
        attempt.status = NotificationStatus.SENT.value
        attempt.error = None
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


def _contact_phone_for(
    session: Session, user: User, *, project_id: uuid.UUID
) -> str | None:
    """Duty line for SMS/voice — an active on-call ``contact_phone`` for this
    user **scoped to the case's project and the user's role**, else the user's
    own phone. Scoping stops a stale/overlapping schedule in another project
    or role from supplying the wrong duty line."""
    now = utcnow()
    row = session.exec(
        select(OnCallSchedule)
        .where(
            OnCallSchedule.user_id == user.id,
            OnCallSchedule.project_id == project_id,
            OnCallSchedule.role == user.role,
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
    idem_ctx: idempotency.IdemContext | None = None,
) -> dict:
    """Every fallback tap writes a ``case_events`` row with the chosen channel.
    The original alert keeps retrying on the device; this only records intent.

    Slice 12 lets the mobile app persist failed tap writes locally and replay
    them later with the same idempotency key, so an offline dial can still land
    in the audit trail without duplicating ``fallback_invoked`` events.
    """
    case, _ = _owned_case(session, case_id=case_id, user=user)
    if idem_ctx is not None:
        try:
            prior = idempotency.check_replay(
                session, idempotency.EMERGENCY_FALLBACK_ENDPOINT, idem_ctx
            )
        except idempotency.IdempotencyConflict as exc:
            raise AuthError(
                409,
                "idempotency_key_conflict",
                "This Idempotency-Key was used with a different request or account.",
            ) from exc
        if prior is not None:
            return {"case_id": case.id, "channel": channel.value, "recorded": True}

    event = CaseEvent(
        project_id=case.project_id,
        case_id=case.id,
        actor_user_id=user.id,
        event_type=FALLBACK_INVOKED_EVENT,
        from_status=case.status,
        to_status=case.status,
        meta={"channel": channel.value},
    )
    session.add(event)
    session.flush()
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
    try:
        if idem_ctx is not None:
            idempotency.stage(
                session,
                endpoint=idempotency.EMERGENCY_FALLBACK_ENDPOINT,
                ctx=idem_ctx,
                resource_id=event.id,
            )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if idem_ctx is not None:
            try:
                prior = idempotency.check_replay(
                    session, idempotency.EMERGENCY_FALLBACK_ENDPOINT, idem_ctx
                )
            except idempotency.IdempotencyConflict as conflict:
                raise AuthError(
                    409,
                    "idempotency_key_conflict",
                    "This Idempotency-Key was used with a different request or account.",
                ) from conflict
            if prior is not None:
                return {"case_id": case.id, "channel": channel.value, "recorded": True}
        raise AuthError(
            409, "fallback_record_conflict", "Fallback tap could not be recorded."
        ) from exc
    return {"case_id": case.id, "channel": channel.value, "recorded": True}


# --------------------------------------------------------------------------- #
# Slice 16 — delivery webhooks                                                #
# --------------------------------------------------------------------------- #


def _find_attempt_by_provider_ref(
    session: Session, *, provider_ref: str, channel: str | None = None
) -> NotificationAttempt | None:
    """Look up a `notification_attempts` row by the provider's reference.
    FCM stores `projects/<id>/messages/<msg>` and Twilio stores its sid;
    both are unique within their channel — for safety we filter by
    channel when the caller knows which one to expect."""
    if not provider_ref:
        return None
    stmt = select(NotificationAttempt).where(
        NotificationAttempt.provider_ref == provider_ref
    )
    if channel is not None:
        stmt = stmt.where(NotificationAttempt.channel == channel)
    return session.exec(stmt).first()


_TERMINAL_STATUSES = {
    NotificationStatus.DELIVERED.value,
    NotificationStatus.FAILED.value,
}


def apply_provider_status(
    session: Session,
    *,
    provider_ref: str,
    channel: str,
    new_status: NotificationStatus,
    error: str | None,
    from_ip: str | None,
) -> NotificationAttempt | None:
    """Webhook-driven status transition for ``notification_attempts``.
    Idempotent: a Twilio retry of the same event re-finds the row already
    in the target state and writes no second audit row.

    Returns the updated attempt, or None if the provider_ref does not
    match any known row (the webhook handler turns that into a 404 so
    a misconfigured callback URL is visible)."""
    attempt = _find_attempt_by_provider_ref(
        session, provider_ref=provider_ref, channel=channel
    )
    if attempt is None:
        return None
    # Idempotency: if the row is already in this terminal state, skip the
    # write entirely. Twilio retries `delivered` -> `delivered`; FCM acks
    # the same provider_ref twice; both must be no-ops.
    if attempt.status == new_status.value:
        return attempt
    # Refuse to walk back from a terminal state. A late "sending" event
    # from Twilio after we already saw "delivered" stays a no-op.
    if attempt.status in _TERMINAL_STATUSES and new_status not in (
        NotificationStatus.DELIVERED,
        NotificationStatus.FAILED,
    ):
        return attempt
    previous = attempt.status
    attempt.status = new_status.value
    if error:
        attempt.error = error[:240]  # bounded so audit stays compact
    elif new_status is NotificationStatus.DELIVERED:
        attempt.error = None
    attempt.attempted_at = utcnow()
    session.add(attempt)
    record_audit(
        session,
        action=AuditAction.EMERGENCY_NOTIFICATION_STATUS_UPDATED,
        actor_user_id=None,  # webhook is unauthenticated by design
        project_id=attempt.project_id,
        resource_type="notification_attempt",
        resource_id=str(attempt.id),
        from_ip=from_ip,
        purpose="emergency.notification_status_webhook",
        meta={
            "channel": channel,
            "from_status": previous,
            "to_status": new_status.value,
            # Provider ref is opaque (Twilio sid / FCM message name);
            # storing it makes the trail searchable without leaking PHI.
            "provider_ref": provider_ref,
        },
        commit=False,
    )
    session.commit()
    session.refresh(attempt)
    return attempt


def acknowledge_fcm_delivery(
    session: Session,
    *,
    user: User,
    provider_ref: str,
    from_ip: str | None,
) -> NotificationAttempt:
    """Slice 16 — mobile device confirms an FCM push arrived. Owner-only:
    the resident POSTing the ack must be the originally-targeted
    recipient (`notification_attempts.recipient_id`). A non-owner gets
    404 with no existence leak, matching the Slice 3/4/6 tenant-isolation
    pattern."""
    attempt = _find_attempt_by_provider_ref(
        session, provider_ref=provider_ref, channel=NotificationChannel.FCM.value
    )
    if attempt is None or attempt.recipient_id != user.id:
        raise AuthError(404, "notification_attempt_not_found", "Unknown notification.")
    # Idempotent: a second ack from the same device is a no-op.
    if attempt.status == NotificationStatus.DELIVERED.value:
        return attempt
    previous = attempt.status
    attempt.status = NotificationStatus.DELIVERED.value
    attempt.error = None
    attempt.attempted_at = utcnow()
    session.add(attempt)
    record_audit(
        session,
        action=AuditAction.EMERGENCY_NOTIFICATION_ACK_RECEIVED,
        actor_user_id=user.id,
        project_id=attempt.project_id,
        resource_type="notification_attempt",
        resource_id=str(attempt.id),
        from_ip=from_ip,
        purpose="emergency.notification_fcm_ack",
        meta={
            "from_status": previous,
            "to_status": NotificationStatus.DELIVERED.value,
            "provider_ref": provider_ref,
        },
        commit=False,
    )
    session.commit()
    session.refresh(attempt)
    return attempt
