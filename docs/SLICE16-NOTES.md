# Slice 16 Notes — Production push + delivery webhooks

> Purpose: capture the Slice 16 surface area, the test cadence, and the
> integration points the next slice should honour. Same shape as
> `SLICE11-HANDOFF.md` / `SLICE14-NOTES.md`.

## Branch state

- Slice 16 lands on the working branch `slice-6-emergency-hardening`
  (the user's stated working base — same as Slices 11–14). `main` is
  still untouched; `docs/SLICE13-MERGE-PLAN.md` records the merge
  plan for Slices 1–12.
- Nothing in this slice merges to `main`.

## What this slice delivers

Closes the brief §14 DoD #2 path (push AND SMS AND voice within 10 s)
on the **backend**. The §14 DoD #1 path (resident on a real Android
device) reached HomeShell in Slice 14; this slice adds the production
push channel that brings the doctor a real notification.

### Backend (full)

1. **Live FCM HTTP v1 push** — `FcmPushGateway` in
   [`apps/backend/app/services/fcm.py`](apps/backend/app/services/fcm.py).
   - Loads the service account file via `google.oauth2.service_account.Credentials`
     (the user's pick: `google-auth` over hand-rolled JWT/OAuth on the
     patient-safety push surface).
   - Refreshes the access token via the library's `Request` transport.
   - POSTs to `/v1/projects/{project_id}/messages:send` with a JSON
     payload that carries the resident's FCM token + alert title +
     alert body. PHI invariant intact: no symptoms / vitals / history
     in the body or `data` channel.
   - 401 → forced refresh + single retry (clock-skew recovery).
   - Missing service account file / project id raises
     `FcmConfigError` *at send time only*. The composite gateway
     constructs successfully at startup even when FCM is unconfigured
     — the Slice 6 fan-out catches the exception per attempt so SMS +
     voice still fire.
2. **CompositeGateway** in
   [`notifications.py`](apps/backend/app/services/notifications.py) —
   joins `FcmPushGateway` (push) and `TwilioNotificationGateway`
   (SMS / voice / WhatsApp) behind the single `NotificationGateway`
   protocol. `get_notification_gateway()` returns the composite when
   `PROVIDER_MODE=live`.
3. **Twilio status callback URL plumbing** — the live Twilio gateway
   now passes `StatusCallback=<configured URL>` on every SMS + voice
   send. Leave `TWILIO_STATUS_CALLBACK_URL` blank to disable callbacks
   (the synchronous `sent` status from the REST call stays the only
   signal — matches pre-Slice-16 behaviour).
4. **`POST /api/v1/notifications/twilio/status`** — webhook handler.
   - `X-Twilio-Signature` is validated against the configured auth
     token via `verify_twilio_signature` (HMAC-SHA1, constant-time
     compare). Invalid sig → `403 invalid_twilio_signature`.
   - Maps `MessageStatus`/`CallStatus` to `NotificationStatus`
     (delivered/failed for terminal events; intermediate states are
     `200 matched: false`, no DB write).
   - Idempotent: retried events stay no-ops; one audit row per
     `(provider_ref, target_status)` transition.
   - Unknown provider_ref → `200 matched: false` (no retry storm).
   - Keys not configured → `200 matched: false` (accept-and-drop for
     stub backends).
5. **`POST /api/v1/notifications/fcm/ack`** — resident-side ack of
   FCM delivery. Owner-only (`recipient_id == actor.id`), idempotent,
   audited. The mobile push handler POSTs `provider_ref` from the
   FCM data payload; backend marks the attempt as `delivered`.
6. **`POST /api/v1/me/device-tokens`** — resident-side counterpart to
   the existing staff `/devices/push-token`. Both share the same
   `register_push_token` service function (upsert by token, audit
   write, tenant-scoped). The staff endpoint stays `alert_feed_roles`
   only — RBAC contract unchanged for prior callers.
7. **New audit actions** in `app/enums.py` and mirrored in
   `packages/shared-types/src/enums.ts`:
   - `DEVICE_TOKEN_REGISTERED`
   - `EMERGENCY_NOTIFICATION_STATUS_UPDATED`
   - `EMERGENCY_NOTIFICATION_ACK_RECEIVED`
8. **New env vars** in `.env.example` and `app/config.py`:
   - `FCM_PROJECT_ID` (required in `PROVIDER_MODE=live` for push)
   - `TWILIO_STATUS_CALLBACK_URL` (optional; HTTPS in production)

### Mobile (seam only, per Option A)

1. **`lib/push/push_token_provider.dart`** — `PushTokenProvider`
   typedef + `nullPushTokenProvider()` default returning null.
2. **`lib/push/device_token_repository.dart`** — `registerToken` and
   `acknowledgePush` against the new backend endpoints. Same
   function-shaped HTTP seam as `AuthRepository` and
   `RecordsRepository`; widget tests stub the typedefs directly.
3. **`main.dart` wiring** — post-login / post-onboarding /
   post-restore call sites all invoke `_registerPushTokenIfAvailable()`,
   which:
   - calls `pushTokenProvider()` (today: null → no-op),
   - on non-null token, POSTs to `/me/device-tokens` with the
     configured platform tag,
   - swallows errors (fire-and-forget; Slice 6 fan-out's SMS + voice
     cover any missing FCM channel).
4. **No new mobile dependencies.** `firebase_messaging` /
   `firebase_core` stay out of `pubspec.yaml` until the Firebase
   project is provisioned. `flutter test` and `flutter analyze` are
   free of platform-channel mocks (the in-memory seam handles it).

## What this slice deliberately does NOT do

- **No `firebase_messaging` adapter on the mobile side.** Deferred to a
  future micro-slice once the Firebase project + per-platform config
  files (`google-services.json` / `GoogleService-Info.plist`) are
  ready. Tracked in `docs/open-questions.md`
  (`[NEEDS_OPS_DECISION] Mobile Firebase project provisioning`).
- **No scheduler infrastructure.** The 60s no-ack backup escalation
  and the stuck-claim reaper still need a runner — that's Slice 17's
  job (per the user's stated next-slice order: 16 → 17 → 15).
- **No webhook re-delivery loop.** Twilio retries non-2xx callbacks
  for ~7 days on their side; the FCM ack path is best-effort from
  the mobile side. We don't add a server-side retry — the existing
  Slice 12 stuck-claim reaper covers the symmetric "provider never
  called back" case (an attempt stuck in `sending`/`sent` is
  re-delivered).
- **No structural change to the Slice 6 fan-out.** Composite gateway
  preserves the "one channel down, others still fire" invariant by
  routing — exceptions still get caught per attempt by the existing
  code.

## Test cadence (Slice 16 baseline)

`ruff` clean. `pytest`: **177 passed** (was 155 pre-Slice-16; **+22**:
Slice 16 added 6 FCM + 14 webhook/ack/device-token tests, and review
#1 added 2 new owner-check tests for the doctor-recipient + cross-
channel cases).
New tests:

| Suite | Coverage |
|---|---|
| `tests/test_fcm.py` (6 tests) | Bearer + URL + payload contract, 401 → refresh + retry, missing project id / missing file / file-not-found / missing `name` field all raise `FcmConfigError` |
| `tests/test_notifications_webhooks.py` (16 tests) | Twilio SMS delivered, voice failed-with-error, invalid signature 403, intermediate state no-op, unknown sid 200/matched:false, replay idempotency, keys-not-configured accept-and-drop. FCM ack against the **doctor recipient** (Slice 16 review #1) + idempotent + unknown-id 404 + resident-cannot-ack-doctor-attempt 404 + unrelated-doctor 404 + cross-channel-id 404. Resident `/me/device-tokens` audit + idempotent upsert. Staff `/devices/push-token` still rejects residents. |

Mobile: `flutter analyze` clean. `flutter test`: **73 passed** (was 64;
**+9** new push tests). `push_test.dart` covers:
- `DeviceTokenRepository.registerToken` happy / empty-token no-op /
  non-2xx false.
- `DeviceTokenRepository.acknowledgePush` happy / empty-ref no-op /
  404 false.
- `SplashScreen` AuthGate: non-null token gets registered, null token
  is a hard no-op (Option A default), thrown provider does not crash
  the home handoff.

Existing baseline holds:
- Backend pre-Slice-16: 155 (Slice 14 tip — the 152 cited in Slice 14's
  commit message was a partial run; full collection at the same SHA
  was 155). After Slice 16 adds `test_fcm.py` (6) and
  `test_notifications_webhooks.py` (14): 175. All Slice 1–14 tests
  still pass. The Slice 14 `test_handover.py` `_FakeSettings` fixture
  was extended with the new `twilio_status_callback_url` /
  `fcm_*` fields (composite gateway constructs at startup with both
  channels lazy-loaded).
- Shared-types typecheck clean.
- Mobile pre-Slice-16: 64 (Slice 14 baseline). All Slice 1–14 widget
  tests still pass.

## Slice 16 review #1 fixes

- **FCM ack path corrected for the real Slice 6 fan-out.** The original
  Slice 16 endpoint owner-checked the actor against
  `notification_attempts.recipient_id` but the recipient is the
  **on-call doctor** (per `_stage_planned_attempts` in
  `emergency_service.py`), not the resident. The endpoint was also
  `resident_only` and the FCM `data` payload carried only a static
  `provider_ref_marker` — there was no field on the device side that
  could be echoed back to identify the originating attempt. As shipped
  in the original Slice 16, the "FCM delivered/ack loop" was not
  reachable in the real doctor-alert path. The original ack tests
  hid this by constructing resident-recipient FCM attempts.
- **Fix.** `notification_attempts.id` (a pre-existing UUID known at
  send time) is now threaded through the FCM `data` payload via the
  `send_push(..., attempt_id=...)` signature. The device-side ack
  posts that id back to `/api/v1/notifications/fcm/ack` (body now
  `{"attempt_id": "<uuid>"}`, not `{"provider_ref": "..."}`). The
  endpoint is open to any authenticated user — the owner check
  (`attempt.recipient_id == actor.id`) is the actual security
  boundary, because the resident is NOT a push recipient in Slice 6
  fan-out. `notification_attempts.provider_ref` continues to hold the
  FCM `name` for cross-reference with the FCM console; the audit row
  carries it in `meta.provider_ref` and the attempt id in
  `resource_id`.
- **New owner-check tests prove the boundary.** Three additions to
  `test_notifications_webhooks.py`:
  `test_resident_cannot_ack_doctor_attempt`,
  `test_fcm_ack_from_unrelated_doctor_returns_404`, and
  `test_fcm_ack_ignores_attempts_on_other_channels` (an SMS / voice
  attempt id posted to the FCM ack endpoint must 404 even if the
  actor IS the recipient — channel-mismatched lookups stay opaque).
  `test_fcm.py`'s happy-path now asserts both `attempt_id` and the
  `provider_ref_marker` show up in the FCM `data` payload.
- **No DB migration.** All changes are at the request/response shape
  + service-layer level; `notification_attempts` columns are
  unchanged. The Slice 16 commit's audit rows are still valid; only
  the meta-field name changed (`provider_ref` is still recorded for
  audit; the `resource_id` slot now carries `attempt_id`).
- **Mobile** `DeviceTokenRepository.acknowledgePush(String attemptId)`
  matches the new contract; `push_test.dart` was updated.

## Integration points the next slice should honour

### 1. Scheduler infra (Slice 17) — webhook handlers are reactive

Slice 17 is going to add the runner for `escalate_stale_alerts` and
`requeue_stuck_notification_attempts`. The Slice 16 webhooks are
strictly reactive (Twilio / device push) — they DO NOT need a
scheduler. But they DO interact with the stuck-claim reaper: a
`delivered` callback that lands BEFORE the reaper's age gate must
preempt the reaper (the `apply_provider_status` function moves the
attempt from `sending` → `delivered` immediately on a valid webhook).
The reaper's age gate (`NOTIFICATION_STUCK_CLAIM_SECONDS`, default
300) is generous enough that this race is rare; if Slice 17's runner
fires more aggressively, verify the order-of-operations holds.

### 2. The provider_ref shape is load-bearing

- Twilio: `MessageSid` for SMS (`SM...`) or `CallSid` for voice
  (`CA...`). Stored verbatim in `notification_attempts.provider_ref`.
- FCM: full `name` field (`projects/<id>/messages/<msg>`). Stored
  verbatim; the mobile ack endpoint matches by exact string.
- A future channel addition (e.g. real WhatsApp delivery callbacks)
  must keep `provider_ref` unique across channels OR pass the
  channel through the ack endpoint to disambiguate. The Slice 16
  ack already filters by `channel = fcm` for safety.

### 3. PHI discipline — webhooks are PHI-free

The webhooks update `notification_attempts.status` + an optional
bounded error string. No message body, no symptoms, no vitals, no
notes ever pass through these endpoints. A future slice that wants
delivery-receipt acknowledgments to carry MORE data must surface that
data via the existing case-detail endpoints (staff-auth, PHI-role-
blocked) — never the webhook.

### 4. Mobile FCM micro-slice (Option B follow-up)

When the Firebase project is provisioned and the config files are
dropped in, the micro-slice is:
- Add `firebase_core: ^3.6.0` and `firebase_messaging: ^15.1.0` to
  `pubspec.yaml`.
- Add `Firebase.initializeApp()` to `main()` (before `runApp`).
- Write a `FirebaseMessagingTokenProvider` that calls
  `FirebaseMessaging.instance.getToken()`.
- Replace `pushTokenProvider: nullPushTokenProvider` with
  `pushTokenProvider: FirebaseMessagingTokenProvider().getToken` in
  `main()`.
- Update `defaultDevicePlatform()` to return `Platform.isAndroid ? 'android' : 'ios'`.
- Write a foreground/background `FirebaseMessaging.onMessage` handler
  that reads the `provider_ref_marker` from `RemoteMessage.data` and
  calls `deviceTokenRepository.acknowledgePush(...)`.
- Widget tests for the picker-style seam: mock the FirebaseMessaging
  channel via `TestDefaultBinaryMessengerBinding`.

That's it. The current 73-test suite + the Slice 16 backend will not
need to change.

### 5. RBAC contract — staff endpoint stays staff-only

The pre-Slice-16 `POST /api/v1/devices/push-token` continues to refuse
residents (the new test `test_staff_endpoint_still_rejects_residents`
locks this in). Slice 14/16/17 widget tests that exercise the staff
endpoint via the dashboard remain unaffected.

## Verification baseline (carry forward)

| Suite | Result |
|---|---|
| Backend `ruff` | All checks passed |
| Backend `alembic check` | No model drift (Slice 16 adds no migrations) |
| Backend `pytest` | **177 passed** (155 → +22 with review #1) |
| `shared-types` typecheck | Clean |
| Dashboard typecheck / lint / build | Unchanged from Slice 14 (no dashboard work this slice) |
| `flutter analyze` | No issues |
| `flutter test` | **73 passed** (64 → +9) |

## Next-slice ordering (per the user's plan)

User's accepted ordering: **16 → 17 → 15**. Slice 16 lands here. Next
up is **Slice 17 — scheduler infra**:
- Pick a runner (Celery / APScheduler / EventBridge → Lambda → API).
- Fire `escalate_stale_alerts` every 30 s.
- Fire `requeue_stuck_notification_attempts` every 60 s.
- Resolve `[NEEDS_OPS_DECISION] Backup-escalation trigger infrastructure`
  in `docs/open-questions.md`.

After Slice 17, **Slice 15 — dashboard rebuild on shadcn/ui +
Tailwind + TanStack Query** with real doctor login and component
decomposition. The dashboard currently consumes the same emergency
API the Slice 16 webhooks update, so the live status changes (sent →
delivered → ack'd) will surface in the dashboard automatically once
the new client polls.
