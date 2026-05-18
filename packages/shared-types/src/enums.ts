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

/** National emergency numbers — the ONLY hardcoded fallback numbers (brief §2.2). */
export const NATIONAL_EMERGENCY_NUMBERS = ["108", "112"] as const;
