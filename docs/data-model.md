# Data Model

> Living document — ER diagram + per-table notes added as tables land (Slice 2+).
> Authoritative refinements: `../PLAN.md` → "Data model refinements".

## Principles

- PostgreSQL, snake_case. Every table: `id UUID`, `created_at`, `updated_at`, and
  `project_id` where tenant-scoped. Multi-tenant by `project` from day one.
- `audit_log` is append-only (UPDATE/DELETE revoked at DB level + trigger guard).
- Soft-delete + 30-day purge job for patient-owned data (DPDP).

## Key refinements over brief §6

- `users.role` includes `security_desk`.
- `consents.consent_type` ∈ { `data_storage`, `emergency_share_with_doctor`,
  `emergency_share_with_hospital`, `family_member_access`,
  `medicine_reminder_notifications`, `data_deletion_processed` }; rows carry
  `granted_at`, `revoked_at`, `policy_version`.
- `projects.enable_security_desk_alerts` (bool, default false).
- `on_call_schedules`, `notification_attempts`, `case_events` added (see PLAN.md).

_Status: skeleton (Slice 1). Schema realised via Alembic from Slice 2._
