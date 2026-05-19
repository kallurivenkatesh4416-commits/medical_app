"""Profile + consent API (PLAN.md Slice 3).

PHI endpoints are role-gated, PHI-role-blocked, and audited. Consent changes
take effect immediately on the next access check.
"""

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import Session

from app.enums import ConsentType, Role
from app.models.user import User
from app.security.deps import (
    client_ip,
    forbid_phi_roles,
    get_db,
    require_roles,
)
from app.services import residents_service

router = APIRouter(prefix="/api/v1", tags=["profile"])

# Module-level dependency singletons (avoids calling require_roles in defaults).
resident_only = require_roles(Role.RESIDENT)
staff_only = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS)


class ConsentUpdateIn(BaseModel):
    granted: bool


@router.get("/me/profile")
def my_profile(
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    return residents_service.read_own_profile(
        session, user=user, from_ip=client_ip(request)
    )


@router.get("/me/consents")
def my_consents(
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> list[dict]:
    resident = residents_service.get_resident_for_user(session, user)
    return [
        {"consent_type": c.consent_type, "granted": c.granted}
        for c in residents_service.list_consents(session, resident.id)
    ]


@router.patch("/me/consents/{consent_type}")
def update_my_consent(
    consent_type: ConsentType,
    body: ConsentUpdateIn,
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    return residents_service.update_consent(
        session,
        user=user,
        consent_type=consent_type,
        granted=body.granted,
        from_ip=client_ip(request),
    )


@router.get(
    "/residents/{resident_id}/profile",
    dependencies=[Depends(forbid_phi_roles)],
)
def staff_view_profile(
    resident_id: uuid.UUID,
    request: Request,
    actor: User = Depends(staff_only),
    session: Session = Depends(get_db),
) -> dict:
    return residents_service.read_profile_as_staff(
        session, actor=actor, resident_id=resident_id, from_ip=client_ip(request)
    )
