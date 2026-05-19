"""Emergency alert API (PLAN.md Slice 5 happy path)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.enums import FallbackChannel, Role
from app.models.user import User
from app.security.deps import client_ip, forbid_phi_roles, get_db, require_roles
from app.services import emergency_service, idempotency
from app.services.auth_service import AuthError
from app.services.residents_service import get_resident_for_user

router = APIRouter(prefix="/api/v1", tags=["emergency"])

resident_only = require_roles(Role.RESIDENT)
alert_feed_roles = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS)
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
    symptom_codes: list[str]
    location_text: str | None
    assigned_doctor_id: uuid.UUID | None
    notification_attempts: list[NotificationAttemptOut]


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
