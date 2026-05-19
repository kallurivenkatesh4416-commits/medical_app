"""Onboarding API (PLAN.md Slice 3): project lookup + resident self-register.

Both endpoints require a short-lived registration token (proves phone
ownership via OTP). docs/onboarding-flow.md documents the screen order.
"""

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.enums import ConsentType
from app.models.project import Project
from app.models.user import User
from app.security.deps import client_ip, get_db, get_registration_phone
from app.services import auth_service, idempotency
from app.services.auth_service import AuthError
from app.services.onboarding_service import (
    ContactInput,
    OnboardingInput,
    complete_onboarding,
)

router = APIRouter(prefix="/api/v1", tags=["onboarding"])


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(pattern=r"^\+?[0-9]{8,15}$")
    relation: str | None = Field(default=None, max_length=60)
    is_primary: bool = False


class ConsentIn(BaseModel):
    consent_type: ConsentType
    granted: bool


class OnboardingIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)
    dob: date
    gender: Literal["male", "female", "other", "prefer_not_to_say"]
    project_id: uuid.UUID
    flat_villa_number: str = Field(min_length=1, max_length=40)
    emergency_contacts: list[ContactIn] = Field(min_length=1, max_length=3)
    disclaimer_acknowledged: bool
    consents: list[ConsentIn]
    blood_group: str | None = Field(default=None, max_length=8)
    diseases: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    surgeries: list[str] = Field(default_factory=list)
    preferred_hospital: str | None = Field(default=None, max_length=160)
    insurance: dict = Field(default_factory=dict)


class OnboardingOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    _phone: str = Depends(get_registration_phone),
    session: Session = Depends(get_db),
) -> list[Project]:
    # Names only — no PHI. Gated by a registration token to avoid open
    # enumeration.
    return list(session.exec(select(Project)).all())


@router.post("/onboarding/complete", response_model=OnboardingOut)
def onboarding_complete(
    body: OnboardingIn,
    request: Request,
    phone: str = Depends(get_registration_phone),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: Session = Depends(get_db),
) -> OnboardingOut:
    ip = client_ip(request)

    def _tokens_for(user: User) -> OnboardingOut:
        access, refresh = auth_service.login_user(session, user=user, from_ip=ip)
        return OnboardingOut(access_token=access, refresh_token=refresh)

    # Idempotent replay — but ONLY for the same verified phone and the same
    # request body. A key replayed by a different phone, or with a different
    # payload, is a conflict (never returns another account's session).
    ctx: idempotency.IdemContext | None = None
    if idempotency_key:
        ctx = idempotency.IdemContext(
            key=idempotency_key,
            owner_fp=idempotency.owner_fingerprint(phone),
            request_fp=idempotency.request_fingerprint(body.model_dump(mode="json")),
        )
        try:
            prior = idempotency.check_replay(
                session, idempotency.ONBOARDING_ENDPOINT, ctx
            )
        except idempotency.IdempotencyConflict as exc:
            raise AuthError(
                409,
                "idempotency_key_conflict",
                "This Idempotency-Key was used with a different request or account.",
            ) from exc
        if prior is not None and prior.user_id is not None:
            existing = session.get(User, prior.user_id)
            if existing is not None:
                return _tokens_for(existing)

    data = OnboardingInput(
        full_name=body.full_name,
        dob=body.dob,
        gender=body.gender,
        project_id=body.project_id,
        flat_villa_number=body.flat_villa_number,
        contacts=[
            ContactInput(
                name=c.name,
                phone=c.phone,
                relation=c.relation,
                is_primary=c.is_primary,
            )
            for c in body.emergency_contacts
        ],
        disclaimer_acknowledged=body.disclaimer_acknowledged,
        consents={c.consent_type: c.granted for c in body.consents},
        blood_group=body.blood_group,
        diseases=body.diseases,
        allergies=body.allergies,
        surgeries=body.surgeries,
        preferred_hospital=body.preferred_hospital,
        insurance=body.insurance,
    )
    try:
        user = complete_onboarding(
            session,
            phone=phone,
            data=data,
            from_ip=ip,
            idem_ctx=ctx,
        )
    except AuthError as exc:
        # Concurrent racer with the same key won the create — replay rather
        # than surfacing the duplicate to a retrying client. Safe: we look up
        # by the CURRENT verified phone, never by the key alone.
        if ctx is not None and exc.code == "already_registered":
            existing = session.exec(
                select(User).where(User.phone == phone)
            ).first()
            if existing is not None:
                return _tokens_for(existing)
        raise
    return _tokens_for(user)
