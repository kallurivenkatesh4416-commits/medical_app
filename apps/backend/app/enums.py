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
    # Slice 8 capability token for the hospital handover PDF (<=15 min). Kept
    # distinct from RECORD_URL so the audit trail can tell the two PHI surfaces
    # apart and a leaked record link can never be replayed against the handover
    # endpoint (and vice versa).
    HANDOVER_URL = "handover_url"


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


class HandoverDispatchChannel(StrEnum):
    """Slice 8: how the hospital handover PDF (signed link) is sent to the
    receiving hospital. Email lands via the dev Mailhog SMTP; WhatsApp lands
    via the Twilio WhatsApp gateway (live keys; stub in dev/CI). The PDF body
    itself is never embedded — only the short-lived signed link is shared."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"


class MedicineFrequency(StrEnum):
    """Slice 9: how often a prescribed medicine recurs. The value is a stable
    wire token (it hits the DB and the mobile notifications scheduler), not a
    display string. ``as_needed`` (PRN) means no fixed times — adherence
    counts treat it as informational only, not missed.
    """

    ONCE_DAILY = "once_daily"
    TWICE_DAILY = "twice_daily"
    THRICE_DAILY = "thrice_daily"
    FOUR_TIMES_DAILY = "four_times_daily"
    WEEKLY = "weekly"
    AS_NEEDED = "as_needed"


class MedicineDoseStatus(StrEnum):
    """Slice 9: per-scheduled-dose log status.

    - ``scheduled``: the slot exists on the schedule but the resident has not
      yet acted on it (transient — only used by the resident's "today" view
      to differentiate upcoming from acted-on doses).
    - ``taken``: resident tapped "I took it" within (or after) the slot.
    - ``skipped``: resident explicitly tapped "Skip".
    - ``missed``: auto-marked when the slot is more than the configured grace
      window in the past with no resident action. The auto-marker is not yet
      a scheduler; for Slice 9 the missed count is computed live from the
      schedule + log when the doctor reads adherence."""

    SCHEDULED = "scheduled"
    TAKEN = "taken"
    SKIPPED = "skipped"
    MISSED = "missed"


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
    OTP_RATE_LIMITED = "otp_rate_limited"
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
    RECORD_UPLOAD_REJECTED = "record_upload_rejected"
    RECORD_LIST = "record_list"
    RECORD_LINK_ISSUED = "record_link_issued"
    EMERGENCY_ALERT_CREATED = "emergency_alert_created"
    EMERGENCY_ALERT_LIST = "emergency_alert_list"
    EMERGENCY_ALERT_ESCALATED = "emergency_alert_escalated"
    EMERGENCY_NOTIFICATION_REQUEUED = "emergency_notification_requeued"
    EMERGENCY_FALLBACK_NUMBERS_READ = "emergency_fallback_numbers_read"
    EMERGENCY_FALLBACK_INVOKED = "emergency_fallback_invoked"
    EMERGENCY_CASE_READ = "emergency_case_read"
    EMERGENCY_CASE_TRANSITIONED = "emergency_case_transitioned"
    CASE_VITAL_RECORDED = "case_vital_recorded"
    CASE_NOTE_RECORDED = "case_note_recorded"
    EMERGENCY_KPI_READ = "emergency_kpi_read"
    # Slice 8 — hospital handover PDF.
    HANDOVER_GENERATED = "handover_generated"
    HANDOVER_LINK_ISSUED = "handover_link_issued"
    HANDOVER_DISPATCHED = "handover_dispatched"
    HANDOVER_DOWNLOADED = "handover_downloaded"
    # Slice 9 — medicine reminders.
    MEDICINE_SCHEDULE_CREATED = "medicine_schedule_created"
    MEDICINE_SCHEDULE_UPDATED = "medicine_schedule_updated"
    MEDICINE_SCHEDULE_LIST = "medicine_schedule_list"
    MEDICINE_DOSE_LOGGED = "medicine_dose_logged"
    MEDICINE_ADHERENCE_READ = "medicine_adherence_read"
    # Slice 10 — aggregate-only admin dashboard and monthly exports.
    ADMIN_KPI_READ = "admin_kpi_read"
    ADMIN_EXPORT_GENERATED = "admin_export_generated"
    # Slice 16 — push delivery surfaces.
    DEVICE_TOKEN_REGISTERED = "device_token_registered"
    EMERGENCY_NOTIFICATION_STATUS_UPDATED = "emergency_notification_status_updated"
    EMERGENCY_NOTIFICATION_ACK_RECEIVED = "emergency_notification_ack_received"
