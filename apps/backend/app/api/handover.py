"""Hospital handover PDF API (PLAN.md Slice 8 / brief §8).

Endpoints:
- POST  /api/v1/emergency/alerts/{case_id}/handover   (doctor) — generate PDF + return signed link
- GET   /api/v1/handover/{handover_id}/link           (staff)  — fresh ≤15-min signed link
- POST  /api/v1/handover/{handover_id}/dispatch       (doctor) — email + WhatsApp dispatch
- GET   /api/v1/handover/file/{token}                 (public, token-gated) — stream the PDF

The signed-file route is unauthenticated by design (it's a capability token
identical in spirit to records/download); the JWT carries the storage key, an
expiry, and a distinct ``handover_url`` type so a leaked record link cannot
fetch a handover (and vice versa).
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.enums import Role
from app.models.user import User
from app.security.deps import client_ip, forbid_phi_roles, get_db, require_roles
from app.services import handover_service

router = APIRouter(prefix="/api/v1", tags=["handover"])

doctor_only = require_roles(Role.DOCTOR)
staff_only = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS)


class HandoverGenerateIn(BaseModel):
    hospital_destination: str = Field(min_length=1, max_length=240)
    doctor_registration_number: str = Field(min_length=1, max_length=80)
    doctor_assessment: str | None = Field(default=None, max_length=2000)


class HandoverOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    project_id: uuid.UUID
    generated_by: uuid.UUID
    generated_at: datetime
    file_name: str
    size_bytes: int
    doctor_name: str
    doctor_registration_number: str
    hospital_destination: str
    signed_url: str | None = None
    expires_in_seconds: int | None = None


class HandoverLinkOut(BaseModel):
    handover_id: uuid.UUID
    url: str
    expires_in_seconds: int


class HandoverDispatchIn(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    whatsapp: str | None = Field(default=None, max_length=32)


class DispatchOut(BaseModel):
    id: uuid.UUID
    handover_id: uuid.UUID
    channel: str
    recipient: str
    status: str
    provider_ref: str | None
    error: str | None
    attempted_at: datetime


class DispatchResultOut(BaseModel):
    handover_id: uuid.UUID
    dispatches: list[DispatchOut]


@router.post(
    "/emergency/alerts/{case_id}/handover",
    response_model=HandoverOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def generate_handover(
    case_id: uuid.UUID,
    body: HandoverGenerateIn,
    request: Request,
    actor: User = Depends(doctor_only),
    session: Session = Depends(get_db),
) -> dict:
    return handover_service.generate_handover(
        session,
        actor=actor,
        case_id=case_id,
        data=handover_service.HandoverInput(
            hospital_destination=body.hospital_destination,
            doctor_registration_number=body.doctor_registration_number,
            doctor_assessment=body.doctor_assessment,
        ),
        from_ip=client_ip(request),
    )


@router.get(
    "/handover/{handover_id}/link",
    response_model=HandoverLinkOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def issue_handover_link(
    handover_id: uuid.UUID,
    request: Request,
    actor: User = Depends(staff_only),
    session: Session = Depends(get_db),
) -> dict:
    return handover_service.issue_link(
        session, actor=actor, handover_id=handover_id, from_ip=client_ip(request)
    )


@router.post(
    "/handover/{handover_id}/dispatch",
    response_model=DispatchResultOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def dispatch_handover(
    handover_id: uuid.UUID,
    body: HandoverDispatchIn,
    request: Request,
    actor: User = Depends(doctor_only),
    session: Session = Depends(get_db),
) -> dict:
    return handover_service.dispatch_handover(
        session,
        actor=actor,
        handover_id=handover_id,
        data=handover_service.DispatchInput(email=body.email, whatsapp=body.whatsapp),
        from_ip=client_ip(request),
    )


@router.get("/handover/file/{token}")
def download_handover(
    token: str, request: Request, session: Session = Depends(get_db)
) -> Response:
    handover, data, name = handover_service.fetch_pdf_for_token(
        session, token=token, from_ip=client_ip(request)
    )
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{name or handover.file_name}"'
        },
    )
