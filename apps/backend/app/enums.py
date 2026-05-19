"""Python mirror of packages/shared-types/src/enums.ts (brief §10: no magic
strings). Values are stable — they hit the DB. A drift check between this file
and the TS source is added in a later slice.
"""

from enum import StrEnum


class Role(StrEnum):
    RESIDENT = "resident"
    FAMILY = "family"
    DOCTOR = "doctor"
    NURSE = "nurse"
    OPS = "ops"
    HOSPITAL = "hospital"
    SECURITY_DESK = "security_desk"
    BUILDER_ADMIN = "builder_admin"
    SUPER_ADMIN = "super_admin"


#: Roles that must NEVER see PHI (RBAC tests enforce this).
NON_PHI_ROLES: frozenset[Role] = frozenset({Role.BUILDER_ADMIN, Role.SECURITY_DESK})


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"
    # Short-lived grant proving phone ownership during self-registration.
    REGISTRATION = "registration"
    # Capability token embedded in a stub signed download URL (<=15 min).
    RECORD_URL = "record_url"


class MedicalRecordType(StrEnum):
    PRESCRIPTION = "prescription"
    LAB = "lab"
    SCAN = "scan"
    DISCHARGE = "discharge"


class CaseStatus(StrEnum):
    ALERTED = "alerted"
    ACKNOWLEDGED = "acknowledged"
    EN_ROUTE = "en_route"
    ON_SITE = "on_site"
    TREATED_ON_SITE = "treated_on_site"
    ESCALATED = "escalated"
    CLOSED = "closed"


class NotificationChannel(StrEnum):
    FCM = "fcm"
    SMS = "sms"
    VOICE = "voice"


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    # Transient claim: one deliverer atomically moves queued -> sending before
    # calling the provider, so a concurrent replay/resume cannot double-send.
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    ACKNOWLEDGED = "acknowledged"


class CaseNoteType(StrEnum):
    OBSERVATION = "observation"
    TREATMENT = "treatment"
    ESCALATION_REASON = "escalation_reason"


class FallbackChannel(StrEnum):
    """Targets on the mobile 60s failed-alert fallback sheet. Every tap writes
    a ``case_events`` row with the chosen channel (PLAN.md Slice 6)."""

    DOCTOR = "doctor"
    EMERGENCY_108 = "emergency_108"
    EMERGENCY_112 = "emergency_112"
    FAMILY_PRIMARY = "family_primary"
    SECURITY_DESK = "security_desk"


class ConsentType(StrEnum):
    DATA_STORAGE = "data_storage"
    EMERGENCY_SHARE_WITH_DOCTOR = "emergency_share_with_doctor"
    EMERGENCY_SHARE_WITH_HOSPITAL = "emergency_share_with_hospital"
    FAMILY_MEMBER_ACCESS = "family_member_access"
    MEDICINE_REMINDER_NOTIFICATIONS = "medicine_reminder_notifications"
    DATA_DELETION_PROCESSED = "data_deletion_processed"


#: The only consent that cannot be declined; revoking starts account closure.
REQUIRED_CONSENT: ConsentType = ConsentType.DATA_STORAGE

#: Consents the resident actively chooses during onboarding (data_deletion_
#: processed is system-set, never user-toggled).
ONBOARDING_CONSENTS: tuple[ConsentType, ...] = (
    ConsentType.DATA_STORAGE,
    ConsentType.EMERGENCY_SHARE_WITH_DOCTOR,
    ConsentType.EMERGENCY_SHARE_WITH_HOSPITAL,
    ConsentType.FAMILY_MEMBER_ACCESS,
    ConsentType.MEDICINE_REMINDER_NOTIFICATIONS,
)

#: Consent policy version. Bumping forces re-consent (trigger is
#: [NEEDS_LEGAL_REVIEW] in compliance-notes.md).
CONSENT_POLICY_VERSION = "2026-05-19.v1"


class AuditAction(StrEnum):
    OTP_REQUESTED = "otp_requested"
    OTP_VERIFY_FAILED = "otp_verify_failed"
    LOGIN_SUCCEEDED = "login_succeeded"
    TOKEN_REFRESHED = "token_refreshed"
    REFRESH_REUSE_BLOCKED = "refresh_reuse_blocked"
    LOGOUT = "logout"
    DISCLAIMER_ACKNOWLEDGED = "disclaimer_acknowledged"
    RESIDENT_REGISTERED = "resident_registered"
    CONSENT_GRANTED = "consent_granted"
    CONSENT_REVOKED = "consent_revoked"
    PATIENT_PROFILE_READ = "patient_profile_read"
    ACCOUNT_CLOSURE_INITIATED = "account_closure_initiated"
    RECORD_UPLOADED = "record_uploaded"
    RECORD_LIST = "record_list"
    RECORD_LINK_ISSUED = "record_link_issued"
    EMERGENCY_ALERT_CREATED = "emergency_alert_created"
    EMERGENCY_ALERT_LIST = "emergency_alert_list"
    EMERGENCY_ALERT_ESCALATED = "emergency_alert_escalated"
    EMERGENCY_FALLBACK_NUMBERS_READ = "emergency_fallback_numbers_read"
    EMERGENCY_FALLBACK_INVOKED = "emergency_fallback_invoked"
    EMERGENCY_CASE_READ = "emergency_case_read"
    EMERGENCY_CASE_TRANSITIONED = "emergency_case_transitioned"
    CASE_VITAL_RECORDED = "case_vital_recorded"
    CASE_NOTE_RECORDED = "case_note_recorded"
    EMERGENCY_KPI_READ = "emergency_kpi_read"
