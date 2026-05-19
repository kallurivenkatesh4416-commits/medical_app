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

_Schema continues to grow per slice._
