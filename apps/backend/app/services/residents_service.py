"""Resident profile reads + consent management (PLAN.md Slice 3).

PHI access is always audited. Consent state is enforced live: revoking a
consent takes effect on the very next access check.
"""

import uuid

from sqlmodel import Session, select

from app.enums import (
    CONSENT_POLICY_VERSION,
    REQUIRED_CONSENT,
    AuditAction,
    ConsentType,
)
from app.models.base import utcnow
from app.models.consent import Consent
from app.models.emergency_contact import EmergencyContact
from app.models.medical_profile import MedicalProfile
from app.models.resident import Resident
from app.models.user import User
from app.services.audit import record_audit
from app.services.auth_service import AuthError, _revoke_all_user_refresh


def get_resident_for_user(session: Session, user: User) -> Resident:
    resident = session.exec(
        select(Resident).where(Resident.user_id == user.id)
    ).first()
    if resident is None:
        raise AuthError(404, "no_resident_profile", "No resident profile for this account.")
    return resident


def list_consents(session: Session, resident_id: uuid.UUID) -> list[Consent]:
    return list(
        session.exec(select(Consent).where(Consent.resident_id == resident_id)).all()
    )


def build_profile(session: Session, resident: Resident) -> dict:
    user = session.get(User, resident.user_id)
    mp = session.exec(
        select(MedicalProfile).where(MedicalProfile.resident_id == resident.id)
    ).first()
    contacts = session.exec(
        select(EmergencyContact)
        .where(EmergencyContact.resident_id == resident.id)
        .order_by(EmergencyContact.priority)  # type: ignore[arg-type]
    ).all()
    return {
        "resident_id": resident.id,
        "full_name": user.full_name if user else None,
        "dob": resident.dob,
        "gender": resident.gender,
        "flat_villa_number": resident.flat_villa_number,
        "project_id": resident.project_id,
        "blood_group": mp.blood_group if mp else None,
        "diseases": mp.diseases if mp else [],
        "allergies": mp.allergies if mp else [],
        "surgeries": mp.surgeries if mp else [],
        "preferred_hospital": mp.preferred_hospital if mp else None,
        "emergency_contacts": [
            {
                "name": c.name,
                "phone": c.phone,
                "relation": c.relation,
                "is_primary": c.is_primary,
            }
            for c in contacts
        ],
        "consents": [
            {"consent_type": c.consent_type, "granted": c.granted}
            for c in list_consents(session, resident.id)
        ],
    }


def assert_consent(
    session: Session, resident_id: uuid.UUID, consent_type: ConsentType
) -> None:
    """Raise 403 if the resident has not granted `consent_type`. Reads live
    state, so a revocation is honoured immediately."""
    row = session.exec(
        select(Consent).where(
            Consent.resident_id == resident_id,
            Consent.consent_type == consent_type.value,
        )
    ).first()
    if row is None or not row.granted:
        raise AuthError(
            403, "consent_required", f"Resident has not consented to {consent_type.value}."
        )


def read_profile_as_staff(
    session: Session, *, actor: User, resident_id: uuid.UUID, from_ip: str | None
) -> dict:
    resident = session.get(Resident, resident_id)
    if resident is None:
        raise AuthError(404, "resident_not_found", "Unknown resident.")
    # Tenant isolation: staff only see residents in their own project.
    if actor.project_id != resident.project_id:
        raise AuthError(404, "resident_not_found", "Unknown resident.")
    # Non-emergency profile sharing is gated by explicit consent (emergency
    # override arrives with the emergency slices).
    assert_consent(session, resident.id, ConsentType.EMERGENCY_SHARE_WITH_DOCTOR)
    record_audit(
        session,
        action=AuditAction.PATIENT_PROFILE_READ,
        actor_user_id=actor.id,
        project_id=resident.project_id,
        resource_type="resident",
        resource_id=str(resident.id),
        from_ip=from_ip,
        purpose="staff.profile_view",
    )
    return build_profile(session, resident)


def read_own_profile(
    session: Session, *, user: User, from_ip: str | None
) -> dict:
    resident = get_resident_for_user(session, user)
    record_audit(
        session,
        action=AuditAction.PATIENT_PROFILE_READ,
        actor_user_id=user.id,
        project_id=resident.project_id,
        resource_type="resident",
        resource_id=str(resident.id),
        from_ip=from_ip,
        purpose="self.profile_view",
    )
    return build_profile(session, resident)


def update_consent(
    session: Session,
    *,
    user: User,
    consent_type: ConsentType,
    granted: bool,
    from_ip: str | None,
) -> dict:
    resident = get_resident_for_user(session, user)
    row = session.exec(
        select(Consent).where(
            Consent.resident_id == resident.id,
            Consent.consent_type == consent_type.value,
        )
    ).first()
    if row is None:
        raise AuthError(404, "consent_not_found", "Unknown consent type for this resident.")

    now = utcnow()

    if consent_type == REQUIRED_CONSENT and not granted:
        # Revoking data_storage starts the account-closure flow.
        row.granted = False
        row.revoked_at = now
        session.add(row)
        user.is_active = False
        user.deleted_at = now
        session.add(user)
        # Stage-only: consent + user soft-delete + token revocation + both
        # audit rows must all commit together (or not at all).
        _revoke_all_user_refresh(session, user.id, commit=False)
        record_audit(
            session,
            action=AuditAction.CONSENT_REVOKED,
            actor_user_id=user.id,
            project_id=resident.project_id,
            resource_type="consent",
            resource_id=consent_type.value,
            from_ip=from_ip,
            purpose="settings.consent",
            commit=False,
        )
        record_audit(
            session,
            action=AuditAction.ACCOUNT_CLOSURE_INITIATED,
            actor_user_id=user.id,
            project_id=resident.project_id,
            resource_type="resident",
            resource_id=str(resident.id),
            from_ip=from_ip,
            purpose="settings.account_closure",
            commit=False,
        )
        session.commit()
        return {"consent_type": consent_type.value, "granted": False, "account_closed": True}

    row.granted = granted
    if granted:
        row.granted_at = now
        row.revoked_at = None
        # Re-consent adopts the current policy version.
        row.policy_version = CONSENT_POLICY_VERSION
    else:
        row.revoked_at = now
    session.add(row)
    record_audit(
        session,
        action=AuditAction.CONSENT_GRANTED if granted else AuditAction.CONSENT_REVOKED,
        actor_user_id=user.id,
        project_id=resident.project_id,
        resource_type="consent",
        resource_id=consent_type.value,
        from_ip=from_ip,
        purpose="settings.consent",
        commit=False,
    )
    session.commit()
    return {"consent_type": consent_type.value, "granted": granted, "account_closed": False}
