# Slice 18 Notes - Hardening Pass

> Handoff for the auth, upload, and Postgres hardening slice.

## Branch state

- Slice 18 lands on `slice-6-emergency-hardening` after Slice 15, 17,
  and 16 review #1. `main` remains untouched.
- Implementation contract: `docs/SLICE18-BRIEF.md`.

## What this slice delivers

### Auth perimeter

- Non-local startup rejects the development JWT secret, short secrets,
  and obvious repeated-character values.
- OTP request and verify work are rate-limited by a DB-backed attempt
  ledger before SMS send or code-attempt mutation.
- Sliding-window defaults are config-driven:
  3 requests per phone per 5 minutes, 30 request attempts per source IP
  per hour, and 10 verify attempts per phone per 5 minutes.
- Rate-limit rejections use the existing error envelope with
  `otp_rate_limited` and write PHI-safe `OTP_RATE_LIMITED` audit rows.
- The Slice 17 scheduler now purges OTP attempt rows older than 24 hours.

### Record upload hardening

- Medical record bytes are checked for the only supported MVP formats:
  PDF, JPEG, and PNG. Declared multipart type must match detected bytes
  before storage.
- Upload rejections write PHI-safe `RECORD_UPLOAD_REJECTED` audit rows.
- Record storage now persists the detected media type.
- `VirusScanGateway` adds a deterministic local stub and a ClamAV
  INSTREAM path selected by `VIRUS_SCAN_MODE`.
- The stub rejects EICAR content for negative-path tests. A configured
  ClamAV path fails closed when clamd is unavailable.

### Production-path verification

- CI keeps the fast SQLite backend job and adds a Postgres backend lane
  for tests marked `postgres_only`.
- The Postgres lane runs migrations and proves refresh-token rotation
  under two concurrent sessions.
- `apps/backend/scripts/load_emergency.py` drives a synthetic concurrent
  emergency-alert run and writes `docs/slice18-load-report.md`.

## Deliberately deferred

- Dashboard HttpOnly cookies and CSRF move to `docs/SLICE18B-BRIEF.md`.
  Slice 15's in-memory access token plus per-tab refresh storage stays
  unchanged in Slice 18.
- AWS deployment, WAF/TLS, observability, secrets management, DPDP
  policy closure, mobile Firebase native wiring, and store release work.

## Verification

| Surface | Result |
|---|---|
| Backend `ruff` | clean |
| Backend SQLite `pytest` | 198 passed, 2 Postgres-only tests skipped |
| Backend Postgres `pytest -m postgres_only` | 2 passed against local Postgres 16 |
| Backend `alembic check` | clean after `0010_otp_attempts` migration |
| Shared types typecheck | clean |
| Dashboard typecheck/lint/build/test | clean; Vitest 5 passed |
| Mobile analyze/test | clean; Flutter 73 passed |

`docs/slice18-load-report.md` records the local Postgres + stub-provider
emergency run: 100 alert creates at concurrency 10, HTTP 200 for all
requests, p50 179.82 ms, p95 591.38 ms, p99 995.62 ms.
