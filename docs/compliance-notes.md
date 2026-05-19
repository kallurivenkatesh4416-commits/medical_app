# Compliance Notes

> **You are not a lawyer. Flag, don't decide.** Every uncertain legal point below is
> tagged `[NEEDS_LEGAL_REVIEW]` for the user's legal advisor. This file is updated
> every slice. Operational (non-legal) unknowns live in `docs/open-questions.md`
> under `[NEEDS_OPS_DECISION]`.

## Hard boundaries enforced in code

- **CDSCO:** No automated diagnosis or treatment recommendation. The MVP records
  vitals, alerts doctors, stores records, coordinates care. No AI/ML. No wearables.
- **No diagnosis language** anywhere — UI, API responses, error messages, logs, PDF.
- **DPDP RBAC:** `builder_admin` never sees PHI. `security_desk` never sees PHI
  beyond the minimum-necessary alert payload (name, flat/villa, location text,
  primary contact phone, case ID).
- **Audit:** every patient-data read/write — and every consent grant/revoke/
  re-consent — writes to the append-only `audit_log` (no UPDATE/DELETE at DB level).
- **Files:** S3 objects encrypted (SSE-S3 min, SSE-KMS preferred); access only via
  signed URLs with ≤ 15-minute TTL.
- **Telemedicine:** consultation notes capture doctor name, registration number,
  consultation timestamp, advice given, patient consent flag (schema from day one).

## Slice 2 implementation notes (auth)

- OTP codes and refresh tokens are never stored or logged in the clear — only
  keyed HMAC-SHA256 hashes at rest; the stub SMS gateway logs a masked recipient
  only, never the code.
- `audit_log` stores a non-reversible phone fingerprint (`phone_fp`), never the
  raw phone number, for pre-auth events.
- `dev_otp` is returned by `POST /auth/otp/request` **only** when
  `APP_ENV=local` (dev/CI convenience). It is suppressed in every other
  environment and an automated test asserts this.
- Append-only audit enforced two ways: app-level SQLAlchemy guard + Postgres
  rule (migration `0001`). `[NEEDS_LEGAL_REVIEW]` item #3 (retention) still open.

## Slice 3 implementation notes (onboarding / consent / profile)

- Onboarding is one transaction (account + profile + contacts + consents +
  audit) — partial registrations cannot occur.
- Every consent grant/revoke/re-consent writes `audit_log`; every PHI profile
  read (self or staff) writes `PATIENT_PROFILE_READ`.
- Consent is enforced live: `assert_consent` reads current state, so a
  revocation blocks the very next access. Non-emergency staff profile view is
  gated by `emergency_share_with_doctor`; the emergency override is a later
  slice and is **not** silently assumed here.
- Revoking `data_storage` triggers account closure: user soft-deleted
  (`deleted_at`), `is_active=false`, all refresh tokens revoked. Hard purge is
  the future 30-day job (DPDP) — `[NEEDS_LEGAL_REVIEW]` item #6 still open.
- `CONSENT_POLICY_VERSION` is recorded per consent row; re-consent adopts the
  current version. The forced-re-consent trigger is `[NEEDS_LEGAL_REVIEW]`
  item #8 (unchanged — implementation does not decide it).
- Staff are tenant-isolated: a resident outside the actor's project returns
  404 (no existence leak).

### Slice 3 review fixes

- data_storage account closure is now atomic: consent revoke + user
  soft-delete + refresh-token revocation + `CONSENT_REVOKED` +
  `ACCOUNT_CLOSURE_INITIATED` audit rows all commit in one transaction (no
  closed-without-audit window).
- Onboarding idempotency: `idempotency_keys` stores only the created
  `user_id`, never access/refresh tokens (no secrets at rest); a retried
  request re-issues fresh tokens for the same account rather than 409.
- Idempotency keys are owner-bound: each row stores a non-reversible owner
  fingerprint (verified phone) and a request fingerprint. A replay is honoured
  only for the SAME verified phone AND the SAME body; any mismatch is a
  `idempotency_key_conflict` (409) — a key cannot be reused by another subject
  to obtain that subject's session.
- `login_user` centrally refuses inactive/soft-deleted accounts
  (`account_inactive` 403). This upholds the closure invariant: once
  `data_storage` is revoked, no path (login, onboarding, idempotent replay)
  can mint fresh credentials for the closed account.

## Slice 4 implementation notes (medical records)

- Medical-record uploads accept PDF/JPEG/PNG only, enforce the configured size
  limit, sanitize download filenames, and store bytes through `StorageGateway`.
- Stub storage encrypts local blobs with a Fernet key derived from the app
  secret. Live storage writes S3 objects with SSE-KMS when configured, falling
  back to SSE-S3 (`AES256`) otherwise.
- Signed download links are hard-capped at 900 seconds regardless of
  `S3_SIGNED_URL_TTL_SECONDS`. The local stub uses JWT capability links;
  live mode uses native S3 presigned GET URLs.
- Resident upload/list/link and staff list/link are audited. Staff access is
  restricted to doctor/nurse/ops, PHI-blocked for builder/security roles,
  tenant-isolated with 404 on cross-project residents, and gated by
  `emergency_share_with_doctor`.
- Upload idempotency is exact-match: same verified resident + same file hash +
  same metadata returns the original record; owner/body mismatch returns
  `idempotency_key_conflict`.

## Slice 5 implementation notes (emergency happy path)

- `POST /emergency/alerts` requires `Idempotency-Key`; exact replay returns the
  original case, while owner/body mismatch returns `idempotency_key_conflict`.
- Slice 5 fans out only the first channel: FCM push to the primary active doctor
  in the resident's project. SMS, voice, backup escalation, and offline retry are
  explicitly Slice 6.
- `notification_attempts` stores channel/status/provider reference/error only.
  The stub gateway logs no alert body, symptoms, or patient history.
- The active dashboard feed is restricted to doctor/nurse/ops, blocks
  builder/security roles, and is tenant-scoped. Security-desk minimum-necessary
  alert payload remains a Slice 6/legal-review boundary.
- Alert creation writes both `audit_log` (`EMERGENCY_ALERT_CREATED`) and
  `case_events` (`alert_created`) so the lifecycle timeline starts at the tap.
- The case/event/audit/idempotency rows are committed before any push is sent;
  a queued `notification_attempts` row is committed before the gateway call and
  then updated to sent/failed. A provider crash cannot leave only an external
  notification with no persisted case.

## Slice 6 implementation notes (emergency hardening)

- The 3-channel fan-out (push/SMS/voice) keeps PHI out of every channel body:
  SMS/push say only "a resident needs medical help, open the app" — no
  symptoms, vitals, or history (SMS/push are not confidential channels). The
  secure detail stays behind auth in the app feed.
- `notification_attempts` still stores **only** channel/status/provider
  ref/error/recipient — never a message body. The stub gateway logs a masked
  recipient only. One channel failing is logged and swallowed; it never blocks
  the case or the other channels.
- Security-desk fan-out is opt-in (`enable_security_desk_alerts`) **and** only
  when a security-desk on-call exists. The constructed payload is exactly the
  minimum-necessary set — name, flat/villa, location text, primary contact
  phone, case id — and nothing else. This remains `[NEEDS_LEGAL_REVIEW]`
  item #7 (the code does not decide that the set is sufficient; it only
  enforces "no more than this").
- Backup escalation transitions **no** case status (lifecycle is Slice 7); it
  only detects "no ack in the configured window", pages the backup, and writes
  `case_events.backup_escalated` + an `EMERGENCY_ALERT_ESCALATED` audit row.
- Every mobile fallback tap writes `case_events.fallback_invoked` (channel
  only) + an `EMERGENCY_FALLBACK_INVOKED` audit row; fallback-number reads
  write `EMERGENCY_FALLBACK_NUMBERS_READ`. The dialer fallback for
  108/112/on-call doctor remains `[NEEDS_LEGAL_REVIEW]` item #9 (unchanged —
  implementation does not resolve it).
- Fallback numbers are resolved from backend state (project / on-call
  schedule / resident contacts); only 108/112 are hardcoded constants
  (brief §2.2). The fallback endpoints are owner-scoped (404 for non-owners,
  no existence leak), consistent with the Slice 3/4 tenant-isolation pattern.
- Concrete live Twilio/FCM remains gated on operator-supplied keys; the
  fan-out/escalation/fallback orchestration is provider-agnostic and fully
  covered against the stub. No diagnosis language in any channel body, audit,
  or log.

### Slice 6 review fixes

- Notification delivery is now durable: queued `notification_attempts` commit
  atomically with the case, so an emergency can never be persisted with no
  notification record, and an interrupted delivery resumes on the idempotent
  retry. Still no body/PHI/symptoms in those rows.
- The resident acknowledgment read (`/emergency/alerts/{id}/status`) is
  owner-scoped and PHI-free (status + a boolean only); a non-owner gets 404
  with no existence leak, matching the Slice 3/4 tenant-isolation pattern.
- The mobile idempotency key is persisted only as an opaque client token (no
  PII) on the device, cleared once the server confirms the case.
- On-call resolution enforces tenant + role on the scheduled user, so a
  misconfigured schedule cannot route an alert (or the security-desk minimal
  payload) to the wrong project or role.

## Open questions — `[NEEDS_LEGAL_REVIEW]`

1. `[NEEDS_LEGAL_REVIEW]` DPDP cross-border data: acceptable AWS S3 region for
   medical files (data localisation expectations).
2. `[NEEDS_LEGAL_REVIEW]` Consent UX wording — is the plain-English summary per
   `consent_type` legally sufficient alongside the "Read full policy" link?
3. `[NEEDS_LEGAL_REVIEW]` Audit-log retention period (how long must `audit_log` be
   retained; interaction with the 30-day purge of patient data).
4. `[NEEDS_LEGAL_REVIEW]` Telemedicine doctor-registration verification — must the
   medical-council registration number be verified, or is self-attestation enough?
5. `[NEEDS_LEGAL_REVIEW]` WhatsApp hospital handover — record-keeping requirements
   for the hospital's opt-in to receive PHI over WhatsApp.
6. `[NEEDS_LEGAL_REVIEW]` Data-deletion request workflow and the legality of the
   soft-delete + 30-day purge approach (and what cannot be deleted, e.g. audit).
7. `[NEEDS_LEGAL_REVIEW]` Security-desk alert payload — confirm "name + flat +
   contact + case id" satisfies the DPDP necessity / minimum-necessary principle.
8. `[NEEDS_LEGAL_REVIEW]` Consent versioning — define the trigger that forces
   re-consent (policy-text change vs scope change vs retention-period change) and
   the re-consent UX.
9. `[NEEDS_LEGAL_REVIEW]` Native dialer fallback (`tel:` for 108 / 112 / on-call
   doctor) — any regulatory implications for an app that programmatically initiates
   emergency calls.

> Add new items here as each slice surfaces them. Never silently resolve a
> `[NEEDS_LEGAL_REVIEW]` item in code.
