"""Emergency alert API (PLAN.md Slice 5 happy path)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.enums import CaseNoteType, CaseStatus, FallbackChannel, Role
from app.models.user import User
from app.security.deps import client_ip, forbid_phi_roles, get_db, require_roles
from app.services import emergency_service, idempotency
from app.services.auth_service import AuthError
from app.services.residents_service import get_resident_for_user

router = APIRouter(prefix="/api/v1", tags=["emergency"])

resident_only = require_roles(Role.RESIDENT)
alert_feed_roles = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS)
lifecycle_roles = require_roles(Role.DOCTOR, Role.NURSE)
kpi_roles = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS, Role.BUILDER_ADMIN)
ops_only = require_roles(Role.OPS)
idempotency_key_header = Header(default=None, alias="Idempotency-Key")


class PushTokenIn(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    platform: str = Field(default="web", max_length=32)


class PushTokenOut(BaseModel):
    registered: bool = True


class AlertIn(BaseModel):
    symptom_codes: list[str] = Field(default_factory=list, max_length=8)
    location_text: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    client_created_at: datetime | None = None


class NotificationAttemptOut(BaseModel):
    channel: str
    recipient_id: uuid.UUID | None
    status: str
    provider_ref: str | None
    error: str | None


class AlertOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    resident_id: uuid.UUID
    resident_name: str | None
    flat_villa_number: str | None
    status: str
    alert_time: datetime
    acknowledged_at: datetime | None
    en_route_at: datetime | None
    on_site_at: datetime | None
    escalated_at: datetime | None
    closed_at: datetime | None
    symptom_codes: list[str]
    location_text: str | None
    assigned_doctor_id: uuid.UUID | None
    resolved_outcome: str | None
    notification_attempts: list[NotificationAttemptOut]


class CaseEventOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    event_type: str
    from_status: str | None
    to_status: str | None
    meta: dict
    created_at: datetime


class CaseVitalIn(BaseModel):
    blood_pressure_systolic: int | None = Field(default=None, ge=40, le=260)
    blood_pressure_diastolic: int | None = Field(default=None, ge=20, le=180)
    spo2_percent: int | None = Field(default=None, ge=0, le=100)
    heart_rate_bpm: int | None = Field(default=None, ge=20, le=260)
    respiratory_rate_bpm: int | None = Field(default=None, ge=4, le=80)
    temperature_c: float | None = Field(default=None, ge=25, le=45)
    notes: str | None = Field(default=None, max_length=240)


class CaseVitalOut(CaseVitalIn):
    id: uuid.UUID
    case_id: uuid.UUID
    project_id: uuid.UUID
    recorded_by: uuid.UUID
    recorded_at: datetime


class CaseNoteIn(BaseModel):
    note_type: CaseNoteType
    body: str = Field(min_length=1, max_length=2000)
    doctor_name: str | None = Field(default=None, max_length=160)
    doctor_registration_number: str | None = Field(default=None, max_length=80)
    consultation_timestamp: datetime | None = None
    advice_given: str | None = Field(default=None, max_length=2000)
    patient_consent_obtained: bool = False


class CaseNoteOut(CaseNoteIn):
    id: uuid.UUID
    case_id: uuid.UUID
    project_id: uuid.UUID
    author_id: uuid.UUID
    created_at: datetime


class AlertDetailOut(AlertOut):
    vitals: list[CaseVitalOut]
    notes: list[CaseNoteOut]
    events: list[CaseEventOut]


class TransitionIn(BaseModel):
    target_status: CaseStatus
    resolved_outcome: str | None = Field(default=None, max_length=240)


class CaseStatusOut(BaseModel):
    case_id: uuid.UUID
    status: str
    acknowledged: bool


class FallbackNumbersOut(BaseModel):
    case_id: uuid.UUID
    doctor: str | None
    emergency_108: str
    emergency_112: str
    family_primary: str | None
    security_desk: str | None


class FallbackTapIn(BaseModel):
    channel: FallbackChannel


class FallbackTapOut(BaseModel):
    case_id: uuid.UUID
    channel: str
    recorded: bool


class EscalationRunOut(BaseModel):
    escalated_case_ids: list[uuid.UUID]


class EmergencyKpiOut(BaseModel):
    project_id: uuid.UUID
    total_cases: int
    active_cases: int
    closed_cases: int
    average_ack_seconds: float | None
    average_on_site_seconds: float | None


@router.post("/devices/push-token", response_model=PushTokenOut)
def register_push_token(
    body: PushTokenIn,
    user: User = Depends(alert_feed_roles),
    session: Session = Depends(get_db),
) -> PushTokenOut:
    emergency_service.register_push_token(
        session, user=user, token=body.token, platform=body.platform
    )
    return PushTokenOut()


@router.post("/emergency/alerts", response_model=AlertOut)
def create_emergency_alert(
    body: AlertIn,
    request: Request,
    idempotency_key: str | None = idempotency_key_header,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    if not idempotency_key:
        raise AuthError(
            422,
            "idempotency_key_required",
            "Idempotency-Key is required for emergency alerts.",
        )
    if len(idempotency_key) > 128:
        raise AuthError(422, "invalid_idempotency_key", "Idempotency-Key is too long.")

    resident = get_resident_for_user(session, user)
    payload = body.model_dump(mode="json")
    ctx = idempotency.IdemContext(
        key=idempotency_key,
        owner_fp=idempotency.owner_fingerprint(user.phone),
        request_fp=idempotency.request_fingerprint(payload),
    )
    replay = emergency_service.replay_alert(session, ctx, resident_id=resident.id)
    if replay is not None:
        return emergency_service.serialize_case(session, replay)

    case = emergency_service.create_alert(
        session,
        user=user,
        data=emergency_service.AlertInput(
            symptom_codes=body.symptom_codes,
            location_text=body.location_text,
            latitude=body.latitude,
            longitude=body.longitude,
            client_created_at=body.client_created_at,
        ),
        from_ip=client_ip(request),
        idem_ctx=ctx,
    )
    return emergency_service.serialize_case(session, case)


@router.get(
    "/emergency/alerts/active",
    response_model=list[AlertOut],
    dependencies=[Depends(forbid_phi_roles)],
)
def active_emergency_alerts(
    request: Request,
    actor: User = Depends(alert_feed_roles),
    session: Session = Depends(get_db),
) -> list[dict]:
    return emergency_service.list_active_alerts(
        session, actor=actor, from_ip=client_ip(request)
    )


@router.get("/emergency/kpis", response_model=EmergencyKpiOut)
def emergency_kpis(
    request: Request,
    actor: User = Depends(kpi_roles),
    session: Session = Depends(get_db),
) -> dict:
    return emergency_service.emergency_kpis(
        session, actor=actor, from_ip=client_ip(request)
    )


@router.get(
    "/emergency/alerts/{case_id}",
    response_model=AlertDetailOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def emergency_case_detail(
    case_id: uuid.UUID,
    request: Request,
    actor: User = Depends(alert_feed_roles),
    session: Session = Depends(get_db),
) -> dict:
    return emergency_service.read_case_detail(
        session, actor=actor, case_id=case_id, from_ip=client_ip(request)
    )


@router.post(
    "/emergency/alerts/{case_id}/transition",
    response_model=AlertOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def transition_emergency_case(
    case_id: uuid.UUID,
    body: TransitionIn,
    request: Request,
    actor: User = Depends(lifecycle_roles),
    session: Session = Depends(get_db),
) -> dict:
    return emergency_service.transition_case(
        session,
        actor=actor,
        case_id=case_id,
        target_status=body.target_status,
        resolved_outcome=body.resolved_outcome,
        from_ip=client_ip(request),
    )


@router.post(
    "/emergency/alerts/{case_id}/vitals",
    response_model=CaseVitalOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def record_case_vitals(
    case_id: uuid.UUID,
    body: CaseVitalIn,
    request: Request,
    actor: User = Depends(lifecycle_roles),
    session: Session = Depends(get_db),
) -> dict:
    return emergency_service.record_vitals(
        session,
        actor=actor,
        case_id=case_id,
        data=emergency_service.VitalInput(
            blood_pressure_systolic=body.blood_pressure_systolic,
            blood_pressure_diastolic=body.blood_pressure_diastolic,
            spo2_percent=body.spo2_percent,
            heart_rate_bpm=body.heart_rate_bpm,
            respiratory_rate_bpm=body.respiratory_rate_bpm,
            temperature_c=body.temperature_c,
            notes=body.notes,
        ),
        from_ip=client_ip(request),
    )


@router.post(
    "/emergency/alerts/{case_id}/notes",
    response_model=CaseNoteOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def record_case_note(
    case_id: uuid.UUID,
    body: CaseNoteIn,
    request: Request,
    actor: User = Depends(lifecycle_roles),
    session: Session = Depends(get_db),
) -> dict:
    return emergency_service.record_case_note(
        session,
        actor=actor,
        case_id=case_id,
        data=emergency_service.NoteInput(
            note_type=body.note_type,
            body=body.body,
            doctor_name=body.doctor_name,
            doctor_registration_number=body.doctor_registration_number,
            consultation_timestamp=body.consultation_timestamp,
            advice_given=body.advice_given,
            patient_consent_obtained=body.patient_consent_obtained,
        ),
        from_ip=client_ip(request),
    )


@router.post("/emergency/escalations/run", response_model=EscalationRunOut)
def run_backup_escalation(
    request: Request,
    actor: User = Depends(ops_only),
    session: Session = Depends(get_db),
) -> EscalationRunOut:
    """Page the backup doctor for alerts with no acknowledgment within the
    configured window. Tenant-scoped to the ops actor's project. This is the
    documented manual entry point until background-worker infra lands; a
    scheduler will call the same service function then."""
    ids = emergency_service.escalate_stale_alerts(
        session,
        project_id=actor.project_id,
        actor_user_id=actor.id,
        from_ip=client_ip(request),
    )
    return EscalationRunOut(escalated_case_ids=ids)


@router.get(
    "/emergency/alerts/{case_id}/status",
    response_model=CaseStatusOut,
)
def emergency_case_status(
    case_id: uuid.UUID,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    """Resident-owned, PHI-free acknowledgment status. The resident polls this
    to decide whether the 60s fallback sheet is needed (the doctor/nurse/ops
    feed is not visible to residents)."""
    return emergency_service.case_status_for_owner(
        session, case_id=case_id, user=user
    )


@router.get(
    "/emergency/alerts/{case_id}/fallback-numbers",
    response_model=FallbackNumbersOut,
)
def emergency_fallback_numbers(
    case_id: uuid.UUID,
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    return emergency_service.fallback_numbers(
        session, case_id=case_id, user=user, from_ip=client_ip(request)
    )


@router.post(
    "/emergency/alerts/{case_id}/fallback",
    response_model=FallbackTapOut,
)
def emergency_fallback_tap(
    case_id: uuid.UUID,
    body: FallbackTapIn,
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    return emergency_service.record_fallback(
        session,
        case_id=case_id,
        user=user,
        channel=body.channel,
        from_ip=client_ip(request),
    )
