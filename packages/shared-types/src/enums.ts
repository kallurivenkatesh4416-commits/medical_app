/**
 * Canonical enums — single source of truth (brief §10: "no magic strings").
 * Backend mirrors these in Python; Flutter mirrors in Dart; CI checks for drift
 * (drift check added in a later slice). Keep values stable: they hit the DB.
 */

export const Role = {
  RESIDENT: "resident",
  FAMILY: "family",
  DOCTOR: "doctor",
  NURSE: "nurse",
  OPS: "ops",
  HOSPITAL: "hospital",
  SECURITY_DESK: "security_desk",
  BUILDER_ADMIN: "builder_admin",
  SUPER_ADMIN: "super_admin",
} as const;
export type Role = (typeof Role)[keyof typeof Role];

/** Roles that must NEVER see PHI (RBAC tests enforce this). */
export const NON_PHI_ROLES: readonly Role[] = [Role.BUILDER_ADMIN, Role.SECURITY_DESK];

export const CaseStatus = {
  ALERTED: "alerted",
  ACKNOWLEDGED: "acknowledged",
  EN_ROUTE: "en_route",
  ON_SITE: "on_site",
  TREATED_ON_SITE: "treated_on_site",
  ESCALATED: "escalated",
  CLOSED: "closed",
} as const;
export type CaseStatus = (typeof CaseStatus)[keyof typeof CaseStatus];

export const ConsentType = {
  DATA_STORAGE: "data_storage",
  EMERGENCY_SHARE_WITH_DOCTOR: "emergency_share_with_doctor",
  EMERGENCY_SHARE_WITH_HOSPITAL: "emergency_share_with_hospital",
  FAMILY_MEMBER_ACCESS: "family_member_access",
  MEDICINE_REMINDER_NOTIFICATIONS: "medicine_reminder_notifications",
  DATA_DELETION_PROCESSED: "data_deletion_processed",
} as const;
export type ConsentType = (typeof ConsentType)[keyof typeof ConsentType];

/** Only consent that cannot be declined; revoking starts account-closure flow. */
export const REQUIRED_CONSENT: ConsentType = ConsentType.DATA_STORAGE;

export const MedicalRecordType = {
  PRESCRIPTION: "prescription",
  LAB: "lab",
  SCAN: "scan",
  DISCHARGE: "discharge",
} as const;
export type MedicalRecordType = (typeof MedicalRecordType)[keyof typeof MedicalRecordType];

export const NotificationChannel = {
  FCM: "fcm",
  SMS: "sms",
  VOICE: "voice",
} as const;
export type NotificationChannel =
  (typeof NotificationChannel)[keyof typeof NotificationChannel];

export const NotificationStatus = {
  QUEUED: "queued",
  /** Transient claim — a deliverer holds the attempt while calling the provider. */
  SENDING: "sending",
  SENT: "sent",
  DELIVERED: "delivered",
  FAILED: "failed",
  ACKNOWLEDGED: "acknowledged",
} as const;
export type NotificationStatus =
  (typeof NotificationStatus)[keyof typeof NotificationStatus];

export const CaseNoteType = {
  OBSERVATION: "observation",
  TREATMENT: "treatment",
  ESCALATION_REASON: "escalation_reason",
} as const;
export type CaseNoteType = (typeof CaseNoteType)[keyof typeof CaseNoteType];

/**
 * Slice 8 — how the hospital handover PDF (short-lived signed link) reaches
 * the receiving hospital. Email lands via the dev Mailhog SMTP; WhatsApp via
 * Twilio WhatsApp (live keys, stub in dev/CI). The PDF body is never embedded
 * — only the signed link is shared so revocation/expiry stays enforceable.
 */
export const HandoverDispatchChannel = {
  EMAIL: "email",
  WHATSAPP: "whatsapp",
} as const;
export type HandoverDispatchChannel =
  (typeof HandoverDispatchChannel)[keyof typeof HandoverDispatchChannel];

/**
 * Slice 9 — how often a prescribed medicine recurs. Stable wire token (hits
 * DB and the mobile reminders scheduler). `as_needed` (PRN) is informational
 * for adherence counts and is never auto-marked as missed.
 */
export const MedicineFrequency = {
  ONCE_DAILY: "once_daily",
  TWICE_DAILY: "twice_daily",
  THRICE_DAILY: "thrice_daily",
  FOUR_TIMES_DAILY: "four_times_daily",
  WEEKLY: "weekly",
  AS_NEEDED: "as_needed",
} as const;
export type MedicineFrequency =
  (typeof MedicineFrequency)[keyof typeof MedicineFrequency];

/**
 * Slice 9 — per-scheduled-dose log status. `scheduled` is the transient
 * upcoming state visible only on the resident's today view; `missed` is
 * computed live from schedule + log when the doctor reads adherence
 * (no scheduler yet).
 */
export const MedicineDoseStatus = {
  SCHEDULED: "scheduled",
  TAKEN: "taken",
  SKIPPED: "skipped",
  MISSED: "missed",
} as const;
export type MedicineDoseStatus =
  (typeof MedicineDoseStatus)[keyof typeof MedicineDoseStatus];

/**
 * Targets on the mobile 60s failed-alert fallback sheet (PLAN.md Slice 6).
 * Every tap records a `case_events` row with the chosen channel.
 */
export const FallbackChannel = {
  DOCTOR: "doctor",
  EMERGENCY_108: "emergency_108",
  EMERGENCY_112: "emergency_112",
  FAMILY_PRIMARY: "family_primary",
  SECURITY_DESK: "security_desk",
} as const;
export type FallbackChannel = (typeof FallbackChannel)[keyof typeof FallbackChannel];

/** Audit action wire tokens mirrored from the backend enum. */
export const AuditAction = {
  OTP_REQUESTED: "otp_requested",
  OTP_VERIFY_FAILED: "otp_verify_failed",
  LOGIN_SUCCEEDED: "login_succeeded",
  TOKEN_REFRESHED: "token_refreshed",
  REFRESH_REUSE_BLOCKED: "refresh_reuse_blocked",
  LOGOUT: "logout",
  DISCLAIMER_ACKNOWLEDGED: "disclaimer_acknowledged",
  RESIDENT_REGISTERED: "resident_registered",
  CONSENT_GRANTED: "consent_granted",
  CONSENT_REVOKED: "consent_revoked",
  PATIENT_PROFILE_READ: "patient_profile_read",
  ACCOUNT_CLOSURE_INITIATED: "account_closure_initiated",
  RECORD_UPLOADED: "record_uploaded",
  RECORD_LIST: "record_list",
  RECORD_LINK_ISSUED: "record_link_issued",
  EMERGENCY_ALERT_CREATED: "emergency_alert_created",
  EMERGENCY_ALERT_LIST: "emergency_alert_list",
  EMERGENCY_ALERT_ESCALATED: "emergency_alert_escalated",
  EMERGENCY_NOTIFICATION_REQUEUED: "emergency_notification_requeued",
  EMERGENCY_FALLBACK_NUMBERS_READ: "emergency_fallback_numbers_read",
  EMERGENCY_FALLBACK_INVOKED: "emergency_fallback_invoked",
  EMERGENCY_CASE_READ: "emergency_case_read",
  EMERGENCY_CASE_TRANSITIONED: "emergency_case_transitioned",
  CASE_VITAL_RECORDED: "case_vital_recorded",
  CASE_NOTE_RECORDED: "case_note_recorded",
  EMERGENCY_KPI_READ: "emergency_kpi_read",
  HANDOVER_GENERATED: "handover_generated",
  HANDOVER_LINK_ISSUED: "handover_link_issued",
  HANDOVER_DISPATCHED: "handover_dispatched",
  HANDOVER_DOWNLOADED: "handover_downloaded",
  MEDICINE_SCHEDULE_CREATED: "medicine_schedule_created",
  MEDICINE_SCHEDULE_UPDATED: "medicine_schedule_updated",
  MEDICINE_SCHEDULE_LIST: "medicine_schedule_list",
  MEDICINE_DOSE_LOGGED: "medicine_dose_logged",
  MEDICINE_ADHERENCE_READ: "medicine_adherence_read",
  ADMIN_KPI_READ: "admin_kpi_read",
  ADMIN_EXPORT_GENERATED: "admin_export_generated",
} as const;
export type AuditAction = (typeof AuditAction)[keyof typeof AuditAction];

/** National emergency numbers — the ONLY hardcoded fallback numbers (brief §2.2). */
export const NATIONAL_EMERGENCY_NUMBERS = ["108", "112"] as const;
