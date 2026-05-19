"""Resident self-registration (PLAN.md Slice 3). The whole onboarding —
account, demographics, emergency contacts, disclaimer ack, consents, medical
profile — is committed in one transaction so it is all-or-nothing.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlmodel import Session, select

from app.enums import (
    CONSENT_POLICY_VERSION,
    ONBOARDING_CONSENTS,
    REQUIRED_CONSENT,
    AuditAction,
    ConsentType,
    Role,
)
from app.models.base import utcnow
from app.models.consent import Consent
from app.models.emergency_contact import EmergencyContact
from app.models.medical_profile import MedicalProfile
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User
from app.services.audit import record_audit
from app.services.auth_service import AuthError


@dataclass
class ContactInput:
    name: str
    phone: str
    relation: str | None = None
    is_primary: bool = False


@dataclass
class OnboardingInput:
    full_name: str
    dob: date
    gender: str
    project_id: uuid.UUID
    flat_villa_number: str
    contacts: list[ContactInput]
    disclaimer_acknowledged: bool
    consents: dict[ConsentType, bool]
    blood_group: str | None = None
    diseases: list | None = None
    allergies: list | None = None
    surgeries: list | None = None
    preferred_hospital: str | None = None
    insurance: dict | None = None


def complete_onboarding(
    session: Session, *, phone: str, data: OnboardingInput, from_ip: str | None
) -> User:
    if not data.disclaimer_acknowledged:
        raise AuthError(422, "disclaimer_required", "The safety disclaimer must be acknowledged.")

    if not 1 <= len(data.contacts) <= 3:
        raise AuthError(422, "invalid_contacts", "Provide between 1 and 3 emergency contacts.")

    missing = set(ONBOARDING_CONSENTS) - set(data.consents)
    if missing:
        raise AuthError(422, "consent_incomplete", "A decision is required for every consent.")
    if not data.consents.get(REQUIRED_CONSENT):
        raise AuthError(
            422, "data_storage_required", "Data storage consent is required to use the app."
        )

    if session.exec(select(User).where(User.phone == phone)).first() is not None:
        raise AuthError(409, "already_registered", "An account already exists for this number.")

    project = session.get(Project, data.project_id)
    if project is None:
        raise AuthError(404, "project_not_found", "Unknown project.")

    now = utcnow()

    user = User(
        project_id=project.id,
        phone=phone,
        role=Role.RESIDENT.value,
        full_name=data.full_name,
    )
    session.add(user)
    session.flush()  # assign user.id without ending the transaction

    resident = Resident(
        user_id=user.id,
        project_id=project.id,
        flat_villa_number=data.flat_villa_number,
        dob=data.dob,
        gender=data.gender,
    )
    session.add(resident)
    session.flush()

    primary_seen = any(c.is_primary for c in data.contacts)
    for idx, c in enumerate(data.contacts):
        session.add(
            EmergencyContact(
                resident_id=resident.id,
                name=c.name,
                phone=c.phone,
                relation=c.relation,
                is_primary=c.is_primary or (not primary_seen and idx == 0),
                priority=idx,
            )
        )

    session.add(
        MedicalProfile(
            resident_id=resident.id,
            blood_group=data.blood_group,
            diseases=data.diseases or [],
            allergies=data.allergies or [],
            surgeries=data.surgeries or [],
            preferred_hospital=data.preferred_hospital,
            insurance=data.insurance or {},
        )
    )

    for ctype in ONBOARDING_CONSENTS:
        granted = bool(data.consents[ctype])
        session.add(
            Consent(
                resident_id=resident.id,
                consent_type=ctype.value,
                granted=granted,
                policy_version=CONSENT_POLICY_VERSION,
                granted_at=now if granted else None,
                revoked_at=None if granted else now,
            )
        )
        record_audit(
            session,
            action=AuditAction.CONSENT_GRANTED if granted else AuditAction.CONSENT_REVOKED,
            actor_user_id=user.id,
            project_id=project.id,
            resource_type="consent",
            resource_id=ctype.value,
            from_ip=from_ip,
            purpose="onboarding.consent",
            meta={"policy_version": CONSENT_POLICY_VERSION},
            commit=False,
        )

    record_audit(
        session,
        action=AuditAction.DISCLAIMER_ACKNOWLEDGED,
        actor_user_id=user.id,
        project_id=project.id,
        from_ip=from_ip,
        purpose="onboarding.disclaimer",
        meta={"policy_version": CONSENT_POLICY_VERSION},
        commit=False,
    )
    record_audit(
        session,
        action=AuditAction.RESIDENT_REGISTERED,
        actor_user_id=user.id,
        project_id=project.id,
        resource_type="resident",
        resource_id=str(resident.id),
        from_ip=from_ip,
        purpose="onboarding.register",
        commit=False,
    )

    # One commit: account + profile + contacts + consents + audit, atomically.
    session.commit()
    session.refresh(user)
    return user
