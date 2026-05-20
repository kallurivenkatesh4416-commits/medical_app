# Slice 12 Hardening Report

Date: 2026-05-20
Branch base: `slice-6-emergency-hardening` at Slice 11 tip `8f92b26`

## Scope

Slice 12 hardens the emergency path called out in `PLAN.md`:

- emergency alert load smoke test
- 2G-style delayed mobile-network simulation
- offline queue plus fallback verification
- at least 80% backend coverage on the emergency API/service path

The run stays in `PROVIDER_MODE=stub`; no live Twilio or FCM keys are required.

## What Changed

- Mobile fallback taps now have a local outbox (`FallbackTapStore`). If the
  device is offline when the resident taps a fallback action, the dial still
  opens immediately and the tap is retried later with the same idempotency key.
- `POST /api/v1/emergency/alerts/{case_id}/fallback` accepts an optional
  `Idempotency-Key`. Replays write no duplicate `fallback_invoked` event or
  audit row; changed body/owner with the same key returns
  `idempotency_key_conflict`.
- Ops can recover notification attempts stuck in `sending` through
  `POST /api/v1/emergency/notifications/requeue-stuck`. The reaper is
  tenant-scoped, age-gated, audited, and redelivers recovered attempts.
- Fresh claims update `notification_attempts.attempted_at`, so the reaper does
  not immediately recycle an active provider call.

## Load And Chaos Proof

Backend test: `test_emergency_alert_load_smoke_dedupes_replays_and_attempts`

- Creates 30 resident emergency alerts through the public API.
- Replays the first 5 idempotency keys with the same body.
- Asserts 30 unique cases, 90 notification attempts, and no duplicate cases on
  replay.

Backend test: `test_stuck_notification_reaper_requeues_and_delivers`

- Creates one old `sending` notification attempt and one fresh `sending`
  attempt.
- Runs the ops reaper with `older_than_seconds=300`.
- Asserts only the old attempt is requeued/redelivered, the fresh provider call
  remains untouched, and `EMERGENCY_NOTIFICATION_REQUEUED` is audited.

Backend test: `test_fallback_tap_idempotency_dedupes_offline_replay`

- Replays a fallback tap with the same idempotency key.
- Asserts exactly one `fallback_invoked` event and one fallback audit row.
- Asserts same-key/different-channel conflict returns 409.

Mobile test: `Offline fallback tap is persisted and synced later`

- Simulates an offline fallback recorder while the dialer still succeeds.
- Asserts the fallback tap is stored locally with its idempotency key.
- Restores connectivity and flushes the outbox, then asserts the pending tap is
  removed.

Mobile test: `FileFallbackTapStore persists, replaces, and removes taps`

- Exercises the production file-backed fallback outbox seam against a temp file.
- Asserts same-key saves replace the pending tap and removal deletes the file.

Mobile test: `2G latency still reaches fallback without duplicate alert sends`

- Simulates delayed alert creation, delayed acknowledgment polling, and delayed
  fallback-number fetches.
- Asserts the user reaches the fallback sheet and the alert send happens once.

## Coverage Gate

Command run from `apps/backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_emergency.py --cov=app.api.emergency --cov=app.services.emergency_service --cov-report=term-missing --cov-fail-under=80
```

Result:

```text
app\api\emergency.py                  185      3    98%
app\services\emergency_service.py     404     41    90%
TOTAL                                 589     44    93%
Required test coverage of 80% reached. Total coverage: 92.53%
27 passed
```

## Residual Risks

- The notification reaper may resend an old attempt if the provider succeeded
  but the process died before the DB write. This is the documented emergency
  tradeoff: age-gated recovery is safer than permanently losing a page.
- The mobile fallback outbox persists only case id, channel, and idempotency
  key. It deliberately does not store resident PHI, phone numbers, symptoms, or
  notes.
- Production timing still depends on the ops scheduler that invokes backup
  escalation and the stuck-claim reaper. The service functions and endpoints are
  now deterministic and idempotent; scheduler infrastructure remains a deploy
  concern.
