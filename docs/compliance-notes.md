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
