# Local Setup

> Goal: a new developer runs the system locally in under 30 minutes. Updated every
> slice. AWS deploy (TLS, rate limiting, WAF, EC2/RDS/S3) is documented here later
> as a separate task ("local now, AWS later").

## Prerequisites

- Docker + Docker Compose
- Python 3.11+ (backend dev outside Docker, optional)
- Node 20+ (dashboard / shared-types)
- Flutter stable (mobile) — optional for backend-only work

## Steps

```bash
cp .env.example .env          # fill local values; never commit .env
docker compose up --build     # postgres + backend + localstack(S3) + mailhog
```

Then:

- Backend health: `GET http://localhost:8000/healthz` and `/readyz`
- API docs: `http://localhost:8000/docs`
- Mailhog UI: `http://localhost:8025`
- In default `PROVIDER_MODE=stub`, medical-record files are stored as encrypted
  local blobs under `LOCAL_STORAGE_DIR`; signed links are served by the backend
  and capped at 15 minutes. `PROVIDER_MODE=live` uses S3/LocalStack via
  `S3_ENDPOINT_URL`.
- Slice 5 dashboard feed: login as a seeded doctor, paste the access token into
  the dashboard, then create a resident alert via `POST /api/v1/emergency/alerts`.
  In `PROVIDER_MODE=stub`, the push attempt is logged as `stub-push`.
- Slice 6: the alert fans out push **+** SMS **+** voice (stub logs
  `stub-push` / `stub-sms` / `stub-voice`, one `notification_attempts` row
  each). Schedule duty via `on_call_schedules`. Drive the no-ack path with
  `POST /api/v1/emergency/escalations/run` as an `ops` user (this is the
  manual trigger until scheduler infra lands). The mobile app's
  "I Need Medical Help" button shows the offline-retry state and, after
  `EMERGENCY_ACK_TIMEOUT_SECONDS` with no ack, the fallback action sheet
  (numbers from `/emergency/alerts/{id}/fallback-numbers`; 108/112 always
  reachable offline). Real Twilio/FCM delivery needs `PROVIDER_MODE=live`
  plus the Twilio + FCM keys in `.env`.
- Slice 7: the dashboard can drive case lifecycle transitions, record vitals,
  and add Telemedicine-complete notes. `GET /api/v1/emergency/kpis` returns
  PHI-free project aggregates for operational dashboards.
- Slice 8: hospital handover PDF. From the dashboard's selected case, a
  doctor generates a §8-complete PDF (`POST /api/v1/emergency/alerts/{id}/handover`),
  receives a 15-minute signed link, can refresh the link
  (`GET /api/v1/handover/{id}/link`), and can dispatch it by email and/or
  WhatsApp (`POST /api/v1/handover/{id}/dispatch`). The shape of the signed
  download URL is provider-mode dependent:
  - `PROVIDER_MODE=stub` (dev/CI): the link is a backend-proxied
    capability link, `GET /api/v1/handover/file/{token}` — the only PHI
    surface that is capability-token-gated rather than auth-gated. The
    token type is distinct from the records token so a leaked record link
    cannot fetch a handover.
  - `PROVIDER_MODE=live`: the link is a native S3 presigned GET issued by
    the StorageGateway, identical to how Slice 4 records work in live
    mode. The backend `/handover/file/{token}` route is not used in live;
    `S3StorageGateway.get_bytes` raises by design (live storage is never
    proxied). `HANDOVER_LINK_ISSUED` is audited at issue time either way.

  Hospital sharing is gated by the resident's `EMERGENCY_SHARE_WITH_HOSPITAL`
  consent on every generate / link refresh / dispatch — a revocation between
  generate and dispatch is honoured immediately.

  Email lands via Mailhog (UI on port 8025) in dev; live SMTP needs
  `SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_USE_TLS`. WhatsApp routes through
  the Twilio gateway (stub in dev, live with `PROVIDER_MODE=live` plus
  `TWILIO_WHATSAPP_FROM`). Without that var, the WhatsApp channel records
  a failed dispatch with error `RuntimeError`.
- Slice 9: medicine reminders. The resident creates a schedule from the
  mobile app (`POST /api/v1/me/medicines/schedules`) which requires the
  live `MEDICINE_REMINDER_NOTIFICATIONS` consent. The mobile app keeps a
  list of device-local reminders in sync with the active schedules
  (no FCM — see `apps/mobile/lib/medicine.dart`); the production
  `flutter_local_notifications` adapter is a thin wrapper around the
  `LocalReminderScheduler` seam and is installed at startup once the
  platform-native channels are configured. Resident self-only:
  `POST /api/v1/me/medicines/doses` records `taken` / `skipped` per slot;
  a network-flake retry returns the existing row (unique index on
  `schedule_id, scheduled_for`). Doctors and nurses read aggregate
  adherence (`GET /api/v1/residents/{id}/medicines/adherence?days=N`,
  `EMERGENCY_SHARE_WITH_DOCTOR` consent-gated, PHI-blocked for
  builder/security). The handover PDF §6 "Current Medicines" section is
  populated from active schedules automatically.
- Slice 10: admin KPIs and monthly export. A `builder_admin` can call
  `GET /api/v1/admin/kpis?days=N` for project-level aggregate counts
  and durations, then download direct monthly exports via
  `GET /api/v1/admin/exports/monthly?month=YYYY-MM&format=csv|pdf`.
  These responses are audited and intentionally contain no patient names,
  flats, case ids, record ids, medicine names, vitals, notes, or signed
  links. Patient drill-in remains blocked by the automated RBAC matrix.
- Slice 11: mobile polish. The resident app requires the full disclaimer
  acknowledgement during onboarding, keeps the short disclaimer on health
  insight screens, exposes `tel:108` from splash/login, adds the Phase-2
  connected-devices settings copy, and applies elderly-UX defaults.
- Slice 12: emergency hardening. Fallback taps now use a mobile local outbox
  plus backend `Idempotency-Key` replay so offline dials can still land in
  `case_events` later without duplicates. Ops can recover provider-crash rows
  with `POST /api/v1/emergency/notifications/requeue-stuck`; the age gate is
  `NOTIFICATION_STUCK_CLAIM_SECONDS`. The load/2G/coverage proof is in
  `docs/slice12-hardening-report.md`.
- Slice 16: production push + delivery webhooks. FCM HTTP v1 push lands
  via `FcmPushGateway` (uses the `google-auth` library for service-account
  credentials and OAuth2 token refresh — set `FCM_SERVICE_ACCOUNT_FILE`
  and `FCM_PROJECT_ID` in `PROVIDER_MODE=live`). `CompositeGateway` joins
  FCM (push) and Twilio (SMS / voice / WhatsApp) behind the
  `NotificationGateway` protocol — the Slice 6 fan-out's
  "one channel down, other two still fire" invariant is preserved
  because each channel's exception stays caught per attempt. Twilio
  status callbacks land at `POST /api/v1/notifications/twilio/status`
  (set `TWILIO_STATUS_CALLBACK_URL` to a publicly-reachable HTTPS URL;
  the handler validates `X-Twilio-Signature` with the configured auth
  token before updating `notification_attempts.status` to
  delivered/failed). FCM delivery acks land at
  `POST /api/v1/notifications/fcm/ack` — resident-auth gated,
  owner-only, idempotent (a second ack from the same device is a no-op).
  Residents now register their own device tokens via
  `POST /api/v1/me/device-tokens` (the staff `/devices/push-token`
  endpoint stays unchanged). Mobile side: `PushTokenProvider` and
  `DeviceTokenRepository` seams + `nullPushTokenProvider` default —
  Option A wiring per the slice-scoping decision, so installing
  `firebase_messaging` plus dropping in `google-services.json` /
  `GoogleService-Info.plist` is a separate micro-slice once the
  Firebase project is provisioned.
- Slice 14: mobile resident path. The app is now demoable end-to-end on a
  real Android device. OTP login is wired against `POST /auth/otp/request`
  + `POST /auth/otp/verify`; tokens live in `flutter_secure_storage`
  (Keystore on Android, Keychain on iOS); `connectivity_plus` drives the
  Slice 11 offline banner; `path_provider` backs the Slice 6 emergency
  outbox so the pending-alert idempotency key now lives in an
  app-private directory (resolves `[NEEDS_OPS_DECISION]` in
  `docs/open-questions.md`). The onboarding wizard walks disclaimer →
  profile → contacts → medical history → consents and submits one atomic
  payload (`POST /onboarding/complete`) with an `Idempotency-Key`. On
  HomeShell, the resident sees their own medicine schedule (with
  per-dose "I took it" / "Skip" buttons), their uploaded medical records
  (open via 15-min signed link), and their consent toggles (live
  `PATCH /me/consents/{type}`); record upload uses `file_picker`. The
  EmergencyApi now reads the live access token on every call via a
  `tokenProvider` closure so a post-login rotation is picked up without
  rebuilding the controller. Run `flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000`
  against an emulator (or your backend's LAN IP for a real device).

### PDF renderer — WeasyPrint → xhtml2pdf substitution

PLAN.md §4 names WeasyPrint as the handover-PDF renderer. WeasyPrint requires
the GTK/Pango native runtime, which is impractical for Windows local dev and
adds an apt/native dep to CI and the backend Docker image. Slice 8 substitutes
**xhtml2pdf** — pure-Python, same Jinja2 HTML template, sufficient for §8's
form-style layout. PDF contents are asserted in tests via `pypdf`. If a richer
CSS engine is required later (e.g. multi-column or advanced flexbox in a
later slice), reintroduce WeasyPrint behind the same `_render_pdf` seam in
`apps/backend/app/services/handover_service.py` and document the GTK install.

## Backend dev (without Docker)

```bash
cd apps/backend
python -m venv .venv
. .venv/Scripts/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
alembic upgrade head
python -m app.seed          # demo project + one user per role
uvicorn app.main:app --reload
pytest
```

### Logging in (dev)

With `APP_ENV=local`, `POST /api/v1/auth/otp/request` returns the code in a
`dev_otp` field so you can complete `POST /api/v1/auth/otp/verify` without a
real SMS provider. Seeded demo phones are `+15550000001`…`+15550000009`
(one per role, in `app.enums.Role` order; generated by `app/seed.py` →
`SEED_PHONES`). `dev_otp` is never exposed outside `local`.

## Required env vars

See `.env.example` (annotated). Provider keys (Twilio/FCM/AWS) are only needed from
the slice that uses them; default `PROVIDER_MODE=stub` needs no external accounts.

_Status: Slices 1-16 cover backend foundation, auth/RBAC/audit,
onboarding/consent/profile, medical-record upload/list/link, the emergency
happy path with dashboard feed, emergency hardening (3-channel fan-out,
on-call resolution, 60s backup escalation, mobile offline retry + fallback
sheet), case lifecycle/vitals/notes/aggregate KPIs, hospital handover
PDF (xhtml2pdf, 15-minute signed link, email + WhatsApp dispatch), and
medicine reminders (resident schedules, device-local reminders, taken/
skipped logs, doctor adherence view, handover §6 wire-in), and the
PHI-free admin dashboard with monthly PDF/CSV export, mobile polish,
Slice 12 emergency hardening (fallback-tap outbox, stuck-notification reaper,
2G/load tests, and 92.53% emergency API/service coverage), and Slice 14
mobile resident-path wire-up (OTP login, onboarding wizard, live medicine /
records / consent surfaces, real `flutter_secure_storage` / `path_provider`
/ `connectivity_plus` / `file_picker`, and live access-token threading
through the Slice 6 emergency API), and Slice 16 production push +
delivery webhooks (FCM HTTP v1 via `google-auth`, composite gateway
joining FCM and Twilio, Twilio status callback handler with signature
validation, resident-side FCM ack endpoint, resident `/me/device-tokens`
endpoint with audit, and a deferred-by-design mobile FCM seam). Live
Twilio (SMS / voice / WhatsApp), live SMTP, and live FCM are wired and
configured per `.env.example`. The only remaining operator-keys task is
mobile Firebase project provisioning (Option A defer): a future
micro-slice swaps `nullPushTokenProvider` for a `firebase_messaging`
adapter once `google-services.json` / `GoogleService-Info.plist` land._
