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

## Realised so far

- **Slice 2** (migration `0001_core_auth`): `projects`, `users`, `otp_codes`,
  `refresh_tokens`, `audit_log`. Secrets (OTP codes, refresh tokens) stored only
  as keyed HMAC-SHA256 hashes. `users.deleted_at` is the soft-delete column.
  `audit_log` has no `updated_at` and is append-only: a SQLAlchemy event blocks
  UPDATE/DELETE (covers SQLite/tests) and a Postgres `RULE ... DO INSTEAD
  NOTHING` blocks it at the DB in prod. `alembic check` verifies model↔migration
  parity in CI scope.

- **Slice 3** (migration `0002_onboarding`): `residents`, `emergency_contacts`,
  `consents` (unique per resident+type, with `policy_version`/`granted_at`/
  `revoked_at`), `medical_profiles` (PHI; JSON lists). Onboarding writes account
  + profile + contacts + consents + audit in **one transaction**. Revoking
  `data_storage` soft-deletes the user and revokes all refresh tokens.

- **Slice 3 review** (migration `0003_idempotency`): `idempotency_keys`
  (unique `endpoint`+`key`; stores `owner_fp` + `request_fp` + the created
  `user_id` or `resource_id` — never tokens). Backs the brief §10 idempotency convention;
  onboarding records its key in the same transaction as the account so
  create + key commit atomically. Replay is owner+request bound (see
  compliance-notes).

- **Slice 4** (migration `0004_medical_records`): `medical_records` stores
  resident/project scope, encrypted object `storage_key`, sanitized filename,
  content type, record type (`prescription`/`lab`/`scan`/`discharge`), optional
  record date/source/tags, size, uploader, timestamps, and `deleted_at`.
  `idempotency_keys.resource_id` points at the created record for exact upload
  replay. Object bytes live outside the DB in the `StorageGateway`.

- **Slice 5** (migration `0005_emergency_happy_path`):
  `emergency_cases` records the resident, project, assigned doctor, alerted
  status, alert timestamp, optional symptoms/location, and future lifecycle
  timestamps. `notification_attempts` stores the Slice 5 FCM push attempt
  status/provider reference/error without message body. `case_events` records
  the initial `alert_created` transition for the timeline/KPI feed. `device_tokens`
  stores staff push tokens for the local/dashboard happy path. Emergency alert
  idempotency also uses `idempotency_keys.resource_id`.

- **Slice 6** (migration `0006_on_call_schedules`): `on_call_schedules`
  (`project_id`, `role`, `user_id`, `starts_at`, `ends_at`, `is_backup`,
  optional `contact_phone` duty line). Fan-out resolves the *active primary*
  for a role; the no-ack timer escalates to the *active backup*. Primary-doctor
  resolution keeps a documented conservative fallback (first active doctor by
  `created_at`) so an alert is never doctor-less; *backup* has no fallback.
  No new emergency tables — backup escalation and fallback taps reuse
  `case_events` (`backup_escalated`, `fallback_invoked`); the
  `backup_escalated` row is the escalation idempotency marker. Lifecycle
  status transitions remain Slice 7.

- **Slice 7** (migration `0007_case_lifecycle`): lifecycle timestamps extend
  `emergency_cases` with `en_route_at` and `escalated_at` (alongside the
  existing `acknowledged_at`, `on_site_at`, `closed_at`). `case_vitals` stores
  manual readings (BP, SpO2, HR, RR, temperature, recorder, timestamp) scoped
  by case/project. `case_notes` stores the timeline note body/type plus
  Telemedicine fields: doctor name, medical-council registration number,
  consultation timestamp, advice given, and patient consent flag. Every
  lifecycle transition, vital entry, and note entry writes both `case_events`
  and `audit_log`.

- **Slice 8** (migration `0008_handover_pdfs`): `handover_pdfs` records one row
  per generated hospital handover PDF — case + project scope, generator user,
  encrypted-object `storage_key` (unique), sanitised file name, size, and
  **frozen** doctor identity (`doctor_name`, `doctor_registration_number`) +
  `hospital_destination` so the signed PDF stays an honest clinical record
  even if the doctor's profile changes later. `handover_dispatches` records
  one row per dispatch attempt (`channel` ∈ `email`/`whatsapp`, plaintext
  `recipient`, `status` reuses `NotificationStatus`, `provider_ref`/`error`,
  `attempted_at`); a failed channel never blocks the other. Case-event types
  `handover_generated` and `handover_dispatched` extend the existing case
  timeline. PHI never leaves the row — outbound mail/WhatsApp carry only the
  short-lived signed link (token type `handover_url`, distinct from
  `record_url`).

- **Slice 9** (migration `0009_medicine_reminders`): `medicine_schedules`
  holds a resident's prescribed medicine + recurrence — project/resident
  scoped, `prescribed_by` (resident self or doctor), `name`, `dose`,
  `instructions`, `frequency` (`MedicineFrequency`), `times_of_day` (JSON
  list of `HH:MM`), `start_date`/`end_date`, `active` flag. The mobile app
  reads this list and schedules **device-local** notifications — no FCM is
  involved (Slice 9 has no external-key dependency). `medicine_dose_logs`
  records one row per resident-acted-on dose: `(schedule_id, scheduled_for)`
  carries a UNIQUE INDEX so a network-flake retry maps to the existing row
  instead of double-logging, `status` ∈ `MedicineDoseStatus` (resident logs
  `taken` / `skipped`; `missed` is computed live from schedule + logs at
  adherence-read time — no scheduler yet). Both tables are written through
  the Slice 3 consent gate (`MEDICINE_REMINDER_NOTIFICATIONS` for create,
  `EMERGENCY_SHARE_WITH_DOCTOR` for staff reads) and every mutation writes
  to `audit_log`. The Slice 8 handover PDF §6 "Current Medicines" section
  is now populated from this table (the §8 placeholder is gone when any
  active schedule exists).

- **Slice 18** (migration `0010_otp_attempts`): `otp_attempts` is the
  pre-auth rate-limit ledger for OTP request and verify paths. It stores a
  keyed phone fingerprint, optional source IP, attempt kind, and attempt
  timestamp so sliding-window limits work across backend instances without
  persisting another raw phone copy. The Slice 17 scheduler purges rows after
  the short hardening retention window.

- **Slice 10**: no new tables. The admin dashboard and monthly export are
  derived from existing tenant-scoped rows (`emergency_cases`, `residents`,
  `medical_records`, `medicine_schedules`, `medicine_dose_logs`, `consents`).
  Export/read activity is captured in `audit_log` via
  `admin_kpi_read` / `admin_export_generated`. The PDF/CSV response is
  streamed directly and intentionally never writes to `StorageGateway`
  because it contains aggregate counts and durations only, not PHI.

- **Slice 12**: no new backend tables. Fallback-tap replay reuses
  `idempotency_keys` with endpoint `emergency.fallback` and
  `resource_id = case_events.id` so the mobile offline outbox can retry the
  same tap without duplicating `fallback_invoked` rows. The stuck-notification
  reaper updates existing `notification_attempts` rows from `sending` back to
  `queued`, audits `emergency_notification_requeued`, and then redelivers.
  The mobile fallback outbox is device-local only and stores case id, channel,
  and idempotency key.

_Schema continues to grow per slice._
