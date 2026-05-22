# Open Questions — Operational

> Operational / business unknowns hit during implementation. Tagged
> `[NEEDS_OPS_DECISION]`. Implementation continues with a sensible **technical
> default** (recorded here) and never invents business policy. Legal questions go to
> `docs/compliance-notes.md` under `[NEEDS_LEGAL_REVIEW]` instead.
>
> The "Builder Pilot Operating Plan" (doctor staffing, night/weekend coverage, SLA
> promises, ambulance contracts, pricing, resident onboarding ops, patient-worsens
> liability) is the user's **separate business document** — not in this repo, not
> stubbed, not committed.

## Items

### `[RESOLVED-SLICE-17]` Backup-escalation trigger infrastructure
- **Original question:** What runs the 60s no-ack backup escalation in
  production — a cron/worker, a queue with delayed messages, or an
  external scheduler?
- **Resolution:** Slice 17 ships `apps/backend/app/scheduler.py` — a
  tiny `run_once`/`run_loop` runner that iterates every project and
  calls the existing idempotent service functions (`escalate_stale_alerts`
  + `requeue_stuck_notification_attempts`). Local/dev wiring: a separate
  `scheduler` service in `docker-compose.yml` runs the same backend
  image with the command overridden to `python -m app.scheduler --mode
  loop`. Production maps cleanly: an ECS Fargate task / Kubernetes
  Deployment running the same image, or AWS EventBridge → Lambda
  driving `--mode once` on a cron rule. Per-project errors are
  isolated; SIGTERM / stop-event preempt the tick sleep; over-firing
  is safe because both service functions are idempotent.
- **Surfaced in:** Slice 17 / `apps/backend/app/scheduler.py`,
  `docker-compose.yml`, `docs/SLICE17-NOTES.md`

### `[RESOLVED-SLICE-14]` Mobile pending-alert store location
- **Original question:** Where should the durable pending-alert file live in
  production — an app-private directory (via `path_provider`) rather than the
  OS temp dir?
- **Resolution:** Slice 14 wires `path_provider`'s
  `getApplicationSupportDirectory()` in `main()` and threads the resulting
  path through `EmergencyApi(appPrivateDir: …)` to both the
  `FilePendingAlertStore` and `FileFallbackTapStore`. The OS temp dir
  remains the test fallback (no platform binding required), so widget
  tests still run with no `path_provider` MethodChannel mock.
- **Surfaced in:** Slice 14 / `apps/mobile/lib/main.dart`,
  `apps/mobile/lib/emergency_api.dart`

### `[RESOLVED-SLICE-12]` Mobile fallback-tap offline durability
- **Original question:** When a fallback button is tapped with no connectivity,
  should the fallback `case_events` write survive offline for the audit/KPI
  trail?
- **Resolution:** Slice 12 adds a mobile fallback-tap outbox. The dialer still
  opens immediately; failed tap writes are persisted locally as case id,
  channel, and idempotency key, then retried later.
- **Backend guard:** `POST /emergency/alerts/{id}/fallback` accepts an optional
  `Idempotency-Key`, so replaying the outbox does not duplicate
  `fallback_invoked` events or audit rows.
- **Surfaced in:** Slice 12 / `apps/mobile/lib/emergency.dart`,
  `apps/mobile/lib/emergency_api.dart`, `app/services/emergency_service.py`

### `[RESOLVED-SLICE-16]` Live FCM HTTP v1 push wiring (backend)
- **Original question:** When the operator provides a Firebase service-
  account file, what is the smallest-blast-radius way to wire FCM HTTP
  v1 alongside the live Twilio gateway?
- **Resolution:** Slice 16 lands `FcmPushGateway` using `google-auth`
  for credential refresh + composite `CompositeGateway` that joins FCM
  (push) and Twilio (SMS / voice / WhatsApp). A missing service-account
  file or project id raises at send-time only — the Slice 6 fan-out
  records FCM as failed without blocking SMS / voice.
- **Surfaced in:** Slice 16 / `apps/backend/app/services/fcm.py`,
  `apps/backend/app/services/notifications.py`

### `[NEEDS_OPS_DECISION]` Mobile Firebase project provisioning
- **Question:** When does the Firebase project + Android / iOS config
  files (`google-services.json` / `GoogleService-Info.plist`) get
  provisioned? This is the only remaining blocker before residents'
  devices can register FCM tokens.
- **Why it matters:** Without a real Firebase project, the
  `PushTokenProvider` seam stays at `nullPushTokenProvider` (Slice 16
  Option A). Token registration becomes a hard no-op and the FCM
  channel of the Slice 6 fan-out always records "no_push_token". SMS
  and voice continue to work — the §14 DoD #2 ten-second SLA is met by
  the other two channels — but real-device push remains untested.
- **Technical default applied:** Slice 16 lands the mobile seam
  (`lib/push/push_token_provider.dart`, `lib/push/device_token_repository.dart`)
  and the post-login registration call site. A future ~30-line slice
  swaps in a `FirebaseMessaging.instance.getToken` adapter once the
  Firebase project is provisioned and the config files are dropped
  into `android/app/` and `ios/Runner/`.
- **Surfaced in:** Slice 16 / `apps/mobile/lib/push/push_token_provider.dart`

## Technical hardening backlog (non-blocking, `[HARDENING]`)

### `[RESOLVED-SLICE-12]` Stuck-claim reaper for notification outbox
- **What:** delivery atomically claims an attempt (`queued → sending`) before
  calling the provider so concurrent delivery/replay cannot double-send. If a
  process dies after claiming but before writing `sent`/`failed`, the row can
  be left in `sending`.
- **Resolution:** Slice 12 adds
  `POST /api/v1/emergency/notifications/requeue-stuck` and
  `emergency_service.requeue_stuck_notification_attempts`. The reaper is
  tenant-scoped, age-gated by `NOTIFICATION_STUCK_CLAIM_SECONDS`, audited, and
  redelivers recovered attempts.
- **Remaining tradeoff:** if the provider succeeded but the process died before
  the DB write, the reaper may resend one old attempt. The age gate keeps this
  out of active provider calls.
- **Surfaced in:** Slice 12 / `emergency_service._claim_attempt`,
  `emergency_service.requeue_stuck_notification_attempts`.

### `[HARDENING]` Postgres-backed concurrency test for refresh rotation
- **What:** `rotate_refresh` relies on `SELECT ... FOR UPDATE` row locking. The
  lock path only exists on Postgres; local/CI tests run on SQLite (which
  ignores `FOR UPDATE`), so the true multi-process race is not exercised.
- **Plan:** add a Postgres service to CI and a concurrency test that fires two
  simultaneous refreshes and asserts exactly one survives and replay revokes
  the family. Fits the Slice 12 hardening pass.
- **Surfaced in:** Slice 2 review (auth_service.rotate_refresh).
