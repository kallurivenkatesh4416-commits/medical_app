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

### Slice 6 review #2 fixes

- Notification delivery is concurrency-safe: an atomic `queued → sending`
  claim makes the provider call at-most-once per attempt, so a lost-response
  replay cannot double-page a doctor/security desk.
- Duty-phone resolution for SMS/voice is scoped to the case's project and the
  recipient's role, closing a path where stale cross-project/role schedule
  data could direct an alert (incl. the security-desk minimal payload) to an
  unintended number.
- The mobile pending record stores only an opaque idempotency key and the
  server case id (no PII/PHI); it is cleared on acknowledgment or the first
  fallback tap.

## Slice 7 implementation notes (case lifecycle)

- Acknowledgment is now a real lifecycle transition: `acknowledged_at` is set
  and status moves off `alerted`, so no-ack escalation and the resident mobile
  fallback status check both stop treating the case as unacknowledged.
- Every lifecycle transition writes `case_events` and `audit_log`. Event names
  are distinct from Slice 6 markers (`backup_escalated`, `fallback_invoked`) so
  escalation idempotency is not disturbed.
- Manual vitals and case notes are PHI. Detail/vitals/notes endpoints are
  restricted to doctor/nurse/ops as appropriate, PHI-blocked for
  `builder_admin`/`security_desk`, tenant-scoped, and audited.
- Treatment notes capture the Telemedicine fields required by the brief:
  doctor name, medical-council registration number, consultation timestamp,
  advice given, and patient consent flag. Item #4 below remains legal-review
  scoped: the code stores the registration number, but does not decide whether
  self-attestation is sufficient.
- Emergency KPIs are aggregate only and contain no resident identifiers or
  clinical detail. `builder_admin` can read the KPI endpoint, but cannot read
  patient-level case detail, vitals, or notes.

## Slice 8 implementation notes (hospital handover PDF)

- The handover PDF is a §8-complete clinical handover (header / patient
  details / complaint / timeline / vitals / history / medicines / observation
  / treatment / escalation reason / family contact / attached-records list /
  signed footer). Generation is **doctor-only** (the doctor's name and
  medical-council registration number are frozen onto the row at generation
  time so the signed PDF stays an honest record even if the profile changes).
- The PDF body lives in encrypted object storage (Slice 4 `StorageGateway`
  — Fernet at rest in dev, SSE-KMS / SSE-S3 in prod). Access is **only** via a
  short-lived signed link, hard-capped at 900 seconds (15 minutes), matching
  the medical-records guarantee.
- The signed-link token type is `handover_url` (distinct from `record_url`)
  so a leaked record link cannot replay the handover endpoint and vice
  versa; the audit trail can tell the two PHI surfaces apart.
- The PDF body is **never embedded** in outbound mail or WhatsApp — only the
  short-lived signed link is shared, so DPDP revocation/expiry stays
  enforceable. WeasyPrint→xhtml2pdf renderer substitution is documented in
  `docs/setup.md`.
- Email dispatch lands via Mailhog in dev (SMTP localhost:1025) and SES /
  Mailgun / SMTP in prod (`PROVIDER_MODE=live`). WhatsApp routes through the
  Twilio gateway (stub in dev, live with `TWILIO_WHATSAPP_FROM`). One channel
  failing is logged on the dispatch row and does not block the other channel
  — same fan-out invariant as Slice 6.
- Every step writes both `case_events` (`handover_generated`,
  `handover_dispatched`) and `audit_log` (`HANDOVER_GENERATED`,
  `HANDOVER_LINK_ISSUED`, `HANDOVER_DISPATCHED`, `HANDOVER_DOWNLOADED`). The
  download audit row has `actor_user_id=null` because the capability-token
  link is unauthenticated by design; the link itself proves authorisation,
  same as `records/download/{token}`.
- Item #5 below (WhatsApp record-keeping for hospital PHI consent) remains
  legal-review scoped — the implementation records dispatch attempts but does
  not decide that the receiving hospital has the right opt-in posture.

### Slice 8 review fixes

- **Hospital consent is now load-bearing.** Generation, link refresh, and
  dispatch all call `assert_consent(resident_id, EMERGENCY_SHARE_WITH_HOSPITAL)`
  before any PHI surface activates. Consent state is read live so a
  revocation between generate and dispatch is honoured immediately; the
  already-stored PDF stays for retention but cannot be re-shared.
- **Live signed link uses the S3 native presigned GET.** `S3StorageGateway.get_bytes`
  raises by design (live storage is never proxied through the backend), so
  in `provider_mode='live'` the handover signed URL comes from the gateway
  and bypasses our `/handover/file/{token}` route entirely. Bucket-side
  access logs cover live audit; we still write `HANDOVER_LINK_ISSUED` at
  the moment the link is minted.
- **Live Twilio gateway implemented.** SMS / voice / WhatsApp now hit
  Twilio's REST API with the Twilio `whatsapp:` prefix added inside the
  gateway (so callers pass a normal phone number). FCM live wiring remains
  a separate operator-keys task and `send_push` raises explicitly on the
  live gateway; Slice 6's fan-out records FCM as failed without blocking
  the other channels.
- **Dispatch is now a durable outbox** (Slice 6 pattern): each
  `handover_dispatches` row is committed in `queued`, atomically claimed
  `queued -> sending` in its own commit, and the provider result (`sent`/
  `failed` + `error`) is committed separately. A hard crash between claim
  and provider response leaves a stuck `sending` row recoverable by a
  reaper — same "lose one notification, never double-send" tradeoff as
  notification_attempts, tracked in `docs/open-questions.md`.

## Slice 9 implementation notes (medicine reminders)

- Schedule creation is gated by **live** `MEDICINE_REMINDER_NOTIFICATIONS`
  consent on the resident — declining at onboarding, or revoking later,
  blocks the very next create with `consent_required` (no schedule row is
  persisted). Same `assert_consent` helper as Slice 8's hospital-consent
  gate.
- Staff schedule reads + adherence reads are gated by
  `EMERGENCY_SHARE_WITH_DOCTOR` (same gate Slice 4 record reads use). PHI
  surfaces are PHI-blocked for `builder_admin` / `security_desk` and
  tenant-isolated (404 cross-project, no existence leak).
- Dose logs are **resident-self-only**: only the resident knows whether
  they actually took the dose. A doctor recording a dose on the resident's
  behalf would be a misleading medical record. A doctor / nurse hitting
  `/me/medicines/doses` gets 403 at the RBAC layer.
- `(schedule_id, scheduled_for)` is uniquely indexed at the DB so a
  network-flake retry on the same slot returns the existing log row
  rather than 409 — the mobile app can safely re-POST without state
  bookkeeping. The service does a pre-check first (returns existing) and
  has an `IntegrityError` race net for genuinely concurrent inserts.
- `missed` doses are **not stored** — they are computed live at
  adherence-read time from the schedule's projected slots minus the logged
  rows, with a 1-hour grace window so a still-pending slot from today is
  not counted as missed. A future hardening item adds a scheduler that
  materializes missed rows (tracked in `docs/open-questions.md`); the
  contract for the doctor's view is the same either way.
- Reminders are **device-local** (`flutter_local_notifications`-style;
  see `apps/mobile/lib/medicine.dart`). No FCM dependency — Slice 9 has
  no operator-key requirement per PLAN.md. The platform-native adapter is
  a separate task; the mobile UI works against an in-memory stub today.
- Slice 8 handover PDF §6 now reflects active schedules. The
  `EMERGENCY_SHARE_WITH_HOSPITAL` gate Slice 8 introduced still controls
  whether the handover (and thus the medicine list) leaves the platform.

### Slice 9 review fixes

- **Reminder revocation actually stops reminders.** `GET /me/medicines/
  schedules` reads `MEDICINE_REMINDER_NOTIFICATIONS` live and returns an
  empty list when revoked; the mobile `LocalReminderScheduler.
  syncFromSchedules([])` then cancels every device-local reminder on the
  next refresh. The DB rows are preserved so re-granting consent restores
  the view without losing the resident's prescription record or dose
  history. The staff list endpoint is intentionally unaffected — a doctor
  must still see what was prescribed even when the resident has paused
  reminders.
- **Dose-log slot integrity.** `log_own_dose` validates that
  `scheduled_for` is a real slot for the schedule — `HH:MM` matches one
  of `times_of_day`, the date lies inside `start_date`..`end_date`, and
  WEEKLY also matches the weekday. Without this, a resident could
  submit `12:34` against an `08:00` schedule and inflate `taken` above
  `scheduled_slots` in the doctor's adherence view. `as_needed` (PRN)
  skips the slot check by design — there are no fixed clock slots.
- **Staff deactivate is owner-checked.** The staff route's path
  `{resident_id}` is now compared against the schedule's owner; a
  mismatch is 404 (no existence leak), so a doctor cannot deactivate
  resident A's schedule by routing through resident B's URL.
- **Schedule validation is tighter.** Duplicate `times_of_day` slots are
  rejected (`duplicate_time_of_day`) so the mobile device never gets two
  reminders for the same slot. `as_needed` schedules are rejected if
  they declare any `times_of_day` (`as_needed_no_slots`) so the PRN
  contract stays honest end-to-end.

### Slice 9 review #2 fix — future-dated dose logs

- **Future-dose guard at write time.** `log_own_dose` rejects any
  `scheduled_for` that is more than `FUTURE_DOSE_TOLERANCE_SECONDS` (120
  s of mobile clock-skew tolerance) in the server's future, with 422
  `future_dose_slot`. Without this guard a schedule starting tomorrow
  could accept a `taken` log for tomorrow's 08:00 slot today, yielding
  `taken: 1, scheduled_slots: 0` in the doctor's adherence view (the
  slot projector only goes up to "now"). Applies to every frequency
  including `as_needed` — a PRN intake that has not happened yet is
  incoherent.
- **Defense in depth on the adherence read.** The adherence query also
  filters `MedicineDoseLog.scheduled_for <= now`, so even if a stray
  future row somehow lands in the DB (clock-skew slip, future
  migration, manual repair script) the doctor's view still satisfies
  `taken + skipped ≤ scheduled_slots`. Two layers, same invariant.

### Slice 9 review #3 fix — tz-aware `scheduled_for` payloads

- **Naive-UTC normalisation at the dose-log entry point.** Pydantic
  parses ISO strings such as `"2026-05-19T08:00:00Z"` or
  `"2026-05-19T13:30:00+05:30"` into offset-aware `datetime` objects;
  the repo convention (see `app/models/base.utcnow`) is naive UTC. The
  service now converts any offset-aware `scheduled_for` to naive UTC at
  the top of `log_own_dose` via `_to_naive_utc`, so the future-dose
  guard's subtraction, the slot-match check, the unique-index lookup,
  and the persisted DB value all speak the same shape. Already-naive
  inputs (assumed UTC) pass through unchanged.

## Slice 10 implementation notes (admin KPIs + monthly export)

- **Aggregate-only admin surface.** `/api/v1/admin/kpis` and
  `/api/v1/admin/exports/monthly` return project-level counts and
  durations only: emergency totals/response times, residents onboarded,
  records uploaded, and medicine adherence totals. They never include
  resident names, flat/villa numbers, case ids, record ids, medicine
  names, vitals, notes, or signed links.
- **Builder visibility stays separate from PHI routes.** `builder_admin`
  can read the aggregate admin endpoints, but every patient-level route
  remains behind `forbid_phi_roles`. Slice 10 extends the automated RBAC
  matrix across real profile, record, emergency, handover, and medicine
  PHI routes for both `builder_admin` and `security_desk`.
- **Monthly export is direct, not a capability link.** The admin PDF/CSV
  is streamed from the request/response path and audited with
  `ADMIN_EXPORT_GENERATED`; it does not use the Slice 4/8
  `StorageGateway`, object storage, or signed URL token types because it
  carries no PHI.
- **Medicine reminder consent is honoured in aggregates.** Residents who
  revoked `MEDICINE_REMINDER_NOTIFICATIONS` are excluded from the project
  adherence totals so paused reminder schedules do not keep contributing
  slots or logs. PRN/as-needed medicine logs are exposed as separate
  aggregate counters (`prn_taken` / `prn_skipped`) and are not used as a
  denominator for scheduled adherence.

## Slice 12 implementation notes (emergency hardening)

- **Offline fallback taps are durable without delaying the call.** The mobile
  fallback outbox stores only `case_id`, `channel`, and an idempotency key.
  It does not store resident names, flat/villa, symptoms, phone numbers,
  vitals, notes, or records. The resident's dialer action still happens even
  when the audit write is offline.
- **Fallback replay is idempotent.** The backend fallback endpoint accepts an
  optional `Idempotency-Key`; exact replays do not duplicate
  `case_events.fallback_invoked` or `EMERGENCY_FALLBACK_INVOKED` audit rows,
  while changed-channel or changed-owner reuse returns
  `idempotency_key_conflict`.
- **Stuck notification recovery is audited and age-gated.** The ops-only
  reaper converts old `notification_attempts(status=sending)` rows back to
  `queued`, records `EMERGENCY_NOTIFICATION_REQUEUED`, and redelivers. A
  provider success followed by a DB crash can still result in one duplicate
  page when the reaper later runs; this is the explicit emergency tradeoff,
  bounded by `NOTIFICATION_STUCK_CLAIM_SECONDS`.
- **Hardening proof lives with the docs.** `docs/slice12-hardening-report.md`
  records the emergency load smoke, 2G latency simulation, offline fallback
  verification, and the 92.53% emergency API/service coverage gate.

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
