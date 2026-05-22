# Slice 18 Brief - Auth, Upload, and Postgres Hardening

> Read this before implementing Slice 18. It is the scope contract for
> the next hardening slice on `slice-6-emergency-hardening`.

## Why this slice exists

Slices 14, 16, 17, and 15 made the end-to-end resident and staff
workflow real:

- Slice 14 wired resident OTP login, onboarding, records, medicines,
  settings, and authenticated emergency taps on mobile.
- Slice 16 added live FCM fan-out and delivery status callbacks.
- Slice 17 added the scheduler that drives backup escalation and the
  stuck notification reaper.
- Slice 15 rebuilt the dashboard around the staff workflow and removed
  the old token-paste UI.

The remaining audit items are now concentrated in the backend
perimeter and production-path verification:

1. A weak or placeholder JWT signing secret can still boot outside dev.
2. OTP request and verify paths are still abuse targets.
3. Medical record uploads still trust caller-declared file type and do
   not pass through a malware-scan seam.
4. CI still proves most concurrency paths on SQLite, while refresh
   rotation and scheduler safety rely on Postgres row locking.

Slice 18 addresses those four items. It is a hardening slice, not a
new product-surface slice.

## Current baseline

Start from the working branch `slice-6-emergency-hardening`.

Recent commits at brief authoring time:

```text
25aa2df feat(dashboard): Slice 15 - rebuild around staff workflow
2646227 feat(scheduler): Slice 17 - runner for backup escalation and stuck-claim reaper
c363b57 fix: Slice 16 review #1 - correct FCM ack path for staff recipients + thread attempt_id through send_push
84886d2 feat(notifications): add production push and delivery webhooks
68b1d35 feat(mobile): implement slice 14 resident path
```

Carry forward these verification baselines:

| Surface | Baseline |
|---|---|
| Backend lint | `ruff` clean |
| Backend tests | `pytest` 186 passed |
| Shared types | typecheck clean |
| Dashboard | typecheck, lint, build clean; Vitest 5 passed |
| Mobile | `flutter analyze` clean; `flutter test` 73 passed |

Read before coding:

- `PROJECT_BRIEF.md` sections 2, 10, 13, and 14.
- `PLAN.md` provider-abstraction and verification sections.
- `docs/SLICE15-NOTES.md`.
- `docs/SLICE17-NOTES.md`.
- `docs/open-questions.md`.
- `docs/compliance-notes.md`.

## Decisions for this slice

### Locked decisions

- Implement Slice 18 as one reviewable slice in the existing cadence:
  implement, verify, commit when asked, then stop for review.
- Keep mobile bearer-token auth unchanged.
- Keep the Slice 15 dashboard session model unchanged for this slice:
  access token in memory, refresh token in `sessionStorage`.
- Defer dashboard HttpOnly cookie auth and CSRF to a follow-up
  `Slice 18b` brief. Do not quietly mix that migration into this
  hardening slice.
- Add Postgres verification in addition to the existing fast SQLite
  suite. Do not replace the SQLite lane.
- Fail closed for rejected or unscanned uploads. Rejected upload bytes
  must never reach storage.

### Out of scope

- AWS infrastructure, TLS termination, WAF, secrets manager wiring, CD,
  observability, and on-call tooling.
- DPDP/legal decisions tracked with `[NEEDS_LEGAL_REVIEW]`.
- Mobile Firebase native wiring. That remains the operator-key follow-up
  tracked in `docs/open-questions.md`.
- Partner hospitals, family portal, store release work, and other later
  production slices.

## Non-negotiable constraints

- No diagnosis language.
- No PHI in logs.
- Audit security-relevant rejections and patient-data access paths.
- Do not log OTP codes, JWT secrets, raw phone numbers, uploaded file
  bytes, or patient file hashes.
- Preserve record upload idempotency, signed-link TTL behavior, RBAC,
  consent checks, emergency fan-out behavior, and scheduler idempotency.
- Mirror new audit enum values in `packages/shared-types/src/enums.ts`.
- Prefer existing repo patterns over new infrastructure. Do not add
  Redis, Celery, RabbitMQ, or a second scheduler for this slice.

## Work package A - Auth perimeter

### A1. Reject weak JWT secrets outside local/test

#### Problem

`apps/backend/app/config.py` still defaults `JWT_SECRET` to the
development placeholder. That is acceptable for local and test startup,
but it must not silently boot in a non-local runtime.

#### Required behavior

- Add one runtime validation path for settings that runs when the app
  starts.
- Outside `APP_ENV in {"local", "test"}`, startup fails when
  `JWT_SECRET`:
  - is the current placeholder;
  - is shorter than 32 characters;
  - is an obviously weak repeated-character value.
- The failure message names `JWT_SECRET` and the reason the runtime is
  refusing to start.
- Local compose and the existing test fixture remain usable with the
  local default.

#### Expected code area

- `apps/backend/app/config.py`
- `apps/backend/app/main.py`
- auth or startup tests under `apps/backend/tests/`
- `.env.example` and setup docs if the runtime requirement needs clearer
  operator copy

#### Tests

- Non-local settings plus the placeholder fail startup validation.
- Non-local settings plus a short or repeated weak secret fail.
- Non-local settings plus a strong secret pass.
- Local/test settings retain the existing no-keys developer path.

### A2. Rate-limit OTP request and verify paths

#### Problem

`request_otp` can send a provider SMS for every accepted request.
`consume_otp` limits attempts on one OTP row, but repeated requests can
still produce repeated verify windows. That leaves SMS-bombing and
brute-force pressure on the auth perimeter.

#### Required behavior

- Enforce sliding-window limits before the side-effecting OTP request or
  verify work proceeds.
- Use these technical defaults:
  - per phone OTP request: 3 attempts per 5 minutes;
  - per source IP OTP request: 30 attempts per hour;
  - per phone OTP verify: 10 attempts per 5 minutes.
- Return the existing error envelope with HTTP 429 and code
  `otp_rate_limited`.
- A limited OTP request must not create a new OTP code or send SMS.
- A limited verify attempt must not consume a code attempt.
- Every rate-limit rejection is audited with a new audit action such as
  `OTP_RATE_LIMITED`.
- Audit meta may include limit kind, window size, observed count, and a
  phone fingerprint. It must not include the raw phone number.
- Add settings for the three default limits to `app/config.py` and
  `.env.example`.

#### Recommended implementation

Use a DB-backed attempt ledger because it works with the current stack,
survives multiple app instances, and needs no new cache service.

Create an auth attempt model and migration that can represent:

- phone fingerprint;
- source IP when present;
- attempt kind, at least request and verify;
- attempt timestamp with the indexes needed for sliding-window counts.

Keep the limit calculation in a small auth service helper so request and
verify call sites share the same logic.

Add cleanup for old attempt rows through the existing scheduler rather
than introducing a new worker. OTP attempts older than 24 hours are not
useful for these windows.

#### Product/UI note

- Mobile already maps `otp_rate_limited` to a resident-friendly message.
- The Slice 15 dashboard currently renders backend error messages through
  its auth client. Add explicit rate-limit copy or a focused UI test if
  implementation changes the user-facing message contract.

#### Tests

- The fourth request for one phone inside five minutes is rejected.
- The thirty-first request from one IP inside one hour is rejected.
- The eleventh verify attempt for one phone inside five minutes is
  rejected.
- A request rejected by the limiter does not produce an OTP send.
- Audit rows for rejection contain fingerprinted metadata only.
- Scheduler cleanup removes stale OTP attempt rows without disturbing
  current-window rows.

### A3. Defer cookie auth and CSRF

Do not move dashboard auth to cookies in Slice 18. That migration changes
backend refresh semantics, dashboard fetch credentials, CSRF token flow,
CORS credentials, and rollout compatibility with the mobile bearer path.

Slice 18 should leave a follow-up brief at `docs/SLICE18B-BRIEF.md`
covering:

- HttpOnly access/refresh cookie shape and TTLs;
- CSRF issue and verification strategy for mutating dashboard requests;
- exact-origin CORS with credentials;
- dashboard `ApiClient` and bootstrap changes;
- compatibility period for existing bearer clients;
- Vitest and backend coverage expected for the migration.

## Work package B - Medical record upload hardening

### B1. Sniff bytes server-side

#### Problem

Record upload validation currently starts from the multipart
`Content-Type`. File extension and declared media type are not a strong
server-side trust boundary.

#### Required behavior

- Sniff uploaded bytes before storage.
- Only PDF, JPEG, and PNG remain accepted.
- Store the sniffed content type, not a client lie.
- Declared type and sniffed type must agree for supported record uploads.
- Unknown, empty, too-short, or undetectable content is rejected.
- Rejection returns a stable API error code. Use
  `file_type_mismatch` for declared-versus-sniffed mismatch and keep
  existing unsupported/size errors for clearly unsupported inputs where
  that matches the existing contract.
- Rejected bytes are not written to storage and no `MedicalRecord` row is
  inserted.
- Write a new audit action such as `RECORD_UPLOAD_REJECTED` with a PHI-safe
  reason and media-type metadata.

#### Recommended implementation

Use `puremagic` for a portable first pass unless the implementation can
meet the same detection tests with a smaller existing dependency. Do not
pull in a native libmagic requirement for the Windows developer path in
this slice.

Place the sniffing and final upload decision in the records service so API
and future callers cannot bypass it.

#### Tests

- A valid PDF declared as PDF still uploads.
- A PNG declared as PDF is rejected and not stored.
- A JPEG declared as PNG is rejected and not stored.
- Empty or signature-insufficient content is rejected.
- Stored `MedicalRecord.content_type` comes from detection, not the
  multipart header.

### B2. Add a virus-scan seam with a real production path

#### Problem

MIME detection does not tell us whether a valid-shape file is safe to
store or serve.

#### Required behavior

- Add a `VirusScanGateway` protocol and a small result type.
- Stub mode stays deterministic and safe for local tests.
- The stub rejects the EICAR test string so the negative path is covered
  without requiring a live scanner.
- Add a ClamAV-backed implementation for the production path.
- Keep scanner selection separate from notification provider mode. Use a
  scanner-specific setting such as `VIRUS_SCAN_MODE=stub|clamav` plus
  `CLAMAV_HOST` and `CLAMAV_PORT`.
- Scan only after basic file validation and MIME detection succeed, and
  before any storage write.
- If the scanner reports malware, return a stable 4xx API error such as
  `record_quarantined`, audit the rejection, and store nothing.
- If the configured production scanner is unavailable, fail closed with a
  clear 5xx/502-style API error such as `record_scan_unavailable`.

#### Production mapping to document

- Local/test default: stub scanner with deterministic EICAR coverage.
- Docker/local optional: ClamAV sidecar or externally reachable clamd.
- AWS production option A: ClamAV reachable from the backend service.
- AWS production option B: later event-driven S3 malware scanning with a
  record quarantine/pending state. Document it as a future design if
  chosen; do not half-build it now.

#### Tests

- Normal PDF path scans clean and persists as before.
- EICAR test bytes are rejected by the stub path.
- Scanner rejection creates a PHI-safe audit row and no record/storage
  artifact.
- Unavailable configured ClamAV path fails closed.

## Work package C - Postgres and stress evidence

### C1. Add a Postgres CI lane

#### Required behavior

- Keep the current backend SQLite CI job.
- Add a second backend Postgres job in `.github/workflows/ci.yml`.
- The job brings up Postgres 15+ as a GitHub Actions service, runs
  migrations, and runs tests marked for Postgres-only behavior.
- Declare a `postgres_only` pytest marker.
- Relax `apps/backend/tests/conftest.py` only enough to permit an
  explicitly named test Postgres database while still failing closed
  against accidental destructive runs on non-test databases.

#### Tests and guardrails

- Default local `pytest` remains SQLite-first and fast.
- `pytest -m postgres_only` can run against a local Postgres test DB.
- Destructive cleanup fixtures cannot point at a production-looking DB.

### C2. Prove refresh rotation under Postgres contention

#### Required behavior

Add a Postgres-only concurrency test for refresh rotation:

- Mint one refresh token.
- Start two independent sessions and two concurrent refresh attempts
  against the same token.
- Assert one attempt wins and one reuse path loses.
- Assert token family revocation and audit trail match the security
  contract.

Resolve the matching `[HARDENING]` item in
`docs/open-questions.md` only after the Postgres test exists.

### C3. Add reproducible emergency-path load evidence

#### Required behavior

- Add an out-of-process script under `apps/backend/scripts/` that can
  create concurrent emergency alerts against a running local backend with
  the stub provider path.
- The script reads its base URL, bearer token, request count, and
  concurrency from environment variables or documented CLI defaults.
- Use unique idempotency keys and synthetic non-PHI payloads.
- Record latency percentiles and failure counts.
- Write a markdown report under `docs/` from a real local run. Do not
  invent numbers.
- Document the command and prerequisites in `docs/setup.md`.

#### Evidence expected

The report should state:

- exact command/config used;
- date/time and target environment;
- request count and concurrency;
- success/failure counts;
- p50, p95, and p99 latency;
- whether the run supports the emergency fan-out performance claim for
  the stub/local path.

## Files likely to change

This is a guide, not permission to widen scope.

- `apps/backend/app/config.py`
- `apps/backend/app/main.py`
- `apps/backend/app/services/auth_service.py`
- auth models and Alembic migration files
- `apps/backend/app/scheduler.py`
- `apps/backend/app/services/records_service.py`
- new virus-scan service module
- backend auth, scheduler, records, and Postgres-only tests
- `apps/backend/pyproject.toml`
- `.github/workflows/ci.yml`
- `.env.example`
- `packages/shared-types/src/enums.ts`
- `docs/setup.md`
- `docs/open-questions.md`
- `docs/SLICE18-NOTES.md`
- `docs/SLICE18B-BRIEF.md`
- load script and load report

## Verification

Run the suites that match the touched surface.

### Backend

From `apps/backend`:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\alembic.exe check
```

Run the Postgres-only lane against a test Postgres database after
migrations:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://medapp:change-me-local-only@localhost:5432/medapp_test"
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m pytest -m postgres_only
```

### Shared types

From `packages/shared-types`:

```powershell
npm run typecheck
```

### Dashboard

From `apps/dashboard`:

```powershell
npm run typecheck
npm run lint
npm run build
npm run test
```

### Mobile

From `apps/mobile`:

```powershell
flutter analyze
flutter test
```

## Slice 18 definition of done

Slice 18 is ready for review when:

1. Non-local startup rejects placeholder and weak JWT secrets.
2. OTP request and verify limits are enforced, audited, configurable,
   and cleaned up by the existing scheduler.
3. Record upload content type is byte-verified before storage.
4. Record upload has a scan gateway, deterministic stub rejection test,
   and documented ClamAV production path.
5. CI includes a Postgres backend lane and a Postgres refresh-race test.
6. A reproducible emergency load script and real local report exist.
7. New audit enums are mirrored in shared types.
8. Setup, open questions, and Slice 18 handoff docs are updated.
9. `docs/SLICE18B-BRIEF.md` exists for cookie auth and CSRF follow-up.
10. Backend, dashboard, shared-types, and mobile verification are green,
    or any non-runnable verification has a precise blocker recorded.

## Stop conditions

Stop and surface the issue instead of guessing if:

- OTP rate limiting requires a policy choice beyond the defaults above.
- Upload scanning requires an AWS-specific asynchronous quarantine model
  rather than the synchronous ClamAV path in this brief.
- Postgres verification reveals an existing refresh-token race contract
  different from the Slice 2 assumptions.
- A change would require resolving a `[NEEDS_LEGAL_REVIEW]` item.

