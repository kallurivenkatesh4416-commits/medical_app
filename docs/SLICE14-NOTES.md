# Slice 14 Notes — Mobile auth + resident screens

> Purpose: capture the Slice 14 surface area, the test cadence, and the
> integration points the next slice should honour. Same shape as
> `SLICE11-HANDOFF.md`.

## Branch state

- Slice 14 lands on the working branch `slice-6-emergency-hardening`
  (per the user's explicit "keep this as the Slice 14 base" direction).
- `docs/SLICE13-MERGE-PLAN.md` records the (separate) merge-plan
  checkpoint for Slices 1–12. Nothing in Slice 14 merges to `main`.

## What this slice delivers

The brief §14 DoD #1 (resident on a real Android device completes the
end-to-end emergency path) is now reachable:

1. **OTP login** — `LoginScreen` wires `POST /auth/otp/request` →
   `POST /auth/otp/verify`. The verify response branches three ways:
   - existing account → token pair persisted, route to `HomeShell`
   - new phone → registration grant persisted, route to `OnboardingFlow`
   - invalid code → inline error, screen stays mounted
2. **Onboarding wizard** — five-step state machine: disclaimer ack →
   profile (name / DOB / gender / project / flat) → emergency contacts
   (1–3) → medical history (blood group / diseases / allergies / surgeries
   / preferred hospital) → consents (5 toggles, `data_storage` locked on).
   Submits one atomic `POST /onboarding/complete` payload with an
   `Idempotency-Key` (retry-safe per Slice 3 backend invariant).
3. **HomeShell live tabs**:
   - **Home** (`LiveHomeTab`) — fetches `GET /me/medicines/schedules` and
     renders per-schedule "I took it" / "Skip" actions that fire
     `POST /me/medicines/doses`.
   - **Vitals** — placeholder text. No resident-self endpoint exists
     (vitals are staff-recorded during emergencies); the empty-state
     copy makes the contract honest.
   - **Records** (`LiveRecordsTab`) — fetches `GET /me/records`, opens
     each via the short-lived signed URL (`GET /me/records/{id}/link`)
     through `url_launcher`. The FAB drives `file_picker` to upload
     PDF/JPG/PNG (`POST /me/records` multipart, idempotency-keyed).
   - **Settings** (`LiveSettingsTab`) — profile summary (read-only),
     canonical Connected-devices copy, consent toggles wired to
     `PATCH /me/consents/{type}` (live revocation honoured on the next
     backend call). Sign out clears `AuthStorage` + revokes the refresh
     token via `POST /auth/logout`.
4. **AuthGate at splash** — on launch, if a stored access token returns a
   resident user from `GET /auth/me`, route directly to `HomeShell`
   (skipping the public splash). Failure / no-session falls back to the
   existing public splash so the offline `Call 108` button stays one tap
   away.
5. **Production plugin wiring** behind the existing seams:
   - `flutter_secure_storage` for JWT (Keystore on Android, Keychain on
     iOS) via `SecureFlutterAuthStorage`.
   - `path_provider` for the emergency outbox — resolves
     `[NEEDS_OPS_DECISION]` from `docs/open-questions.md`.
   - `connectivity_plus` for the offline banner via
     `ConnectivityPlusWatcher`.
   - `file_picker` for record upload (registered lazily so widget tests
     never load the plugin).
6. **EmergencyApi token threading** — accepts a `tokenProvider`
   closure; production wires it to `AuthStorage.readAccessToken` so a
   post-login rotation is picked up on every call without rebuilding the
   controller.

## What this slice deliberately does NOT do

- **No backend changes** (per the user's approved fork). Editing the
  resident profile or emergency contacts after onboarding is a future
  mini-slice — there is no `PATCH /me/medical-profile` or contacts CRUD
  endpoint yet.
- **No FCM device-token registration**. Push delivery is Slice 16's job
  (separate operator-keys task). Today's `EmergencyApi` still depends on
  the backend stub gateway for push attempts.
- **No real `flutter_local_notifications` adapter**. The Slice 9
  `LocalReminderScheduler` seam continues to use the in-memory stub;
  production wiring sits behind the same seam and lands when the
  platform-native channels are configured.
- **No family-member portal**. The `family_member_access` consent is
  togglable, but the family-member login + read-only view of the
  resident's profile is a separate slice.

## Test cadence (Slice 14 baseline)

`flutter analyze` — clean. `flutter test` — **64 passed** (up from
Slice 12's 19). New suites:

| Suite | What it covers |
|---|---|
| `test/auth_test.dart` | `InMemoryAuthStorage` contract, `AuthRepository` (request OTP / verify branches / refresh success+failure / logout idempotency), `SplashScreen` AuthGate routing |
| `test/onboarding_test.dart` | Wizard walks all five steps, submits the complete payload, persists the session; failure surfaces an error and keeps the wizard mounted |
| `test/home_live_test.dart` | `LiveHomeTab` schedules + dose buttons, `LiveRecordsTab` list + signed-link launcher, `LiveSettingsTab` consent toggle + sign out flow |

The 19 pre-existing tests (`emergency_test.dart`, `medicine_test.dart`,
`polish_test.dart`, `widget_test.dart`) **all still pass unchanged** —
new repos / seams default to null in widget tests so the legacy
constructor-driven shells stay rendered.

## Integration points the next slice should honour

### 1. Auth storage is the JWT source of truth

`flutter_secure_storage` (production) or `InMemoryAuthStorage` (tests).
Slice 16's FCM device-token registration MUST NOT cache the access
token on the device side — `ApiClient.refreshTokens` already handles
401 → refresh → replay; a Slice 16 push-token POST should just use the
same client.

### 2. `OnboardingPayload.toJson` shape is load-bearing

The backend's idempotency invariant (`req_fp` is a fingerprint of the
JSON body) means **changing the field order or shape in
`OnboardingPayload.toJson()` invalidates in-flight registration
retries**. A future field addition must keep prior fields stable.

### 3. The Slice 11 polish surfaces stay reachable in BOTH modes

- Live tabs and legacy tabs both carry the sticky `DISCLAIMER_SHORT`
  footer via `_HealthScreenScaffold`.
- Settings still renders the canonical `CONNECTED_DEVICES_PHASE2` copy
  literally (no paraphrasing — wording history is in `docs/ux-copy.md`).
- The splash + login `Call 108` buttons are unchanged; the AuthGate
  only routes past the splash when a resident token resolves.

### 4. EmergencyApi tokenProvider is read on every call

Slice 6/12 invariants still hold (idempotency key, fallback outbox,
durability across kills). The Slice 14 change is only how the access
token is sourced. A Slice 16 FCM service-account flow that issues a
NEW access token via the refresh path will be picked up automatically.

### 5. Picker seam is null on the test path

`recordsPickerUploader` is registered exactly once by
`registerRecordsPickerUploader()` in `main()`. Widget tests bypass
`main` and the seam stays null — tapping the FAB in a test is a no-op
unless the test provides its own `testUploadHook` on `LiveRecordsTabState`.

## Verification baseline (carry forward)

| Suite | Result |
|---|---|
| Backend `ruff` | All checks passed |
| Backend `alembic upgrade head` + `alembic check` | No model drift |
| Backend `pytest` | 152 passed (unchanged from Slice 12) |
| `shared-types` typecheck | Clean (unchanged) |
| Dashboard typecheck / lint / build | Clean (unchanged) |
| `flutter analyze` | No issues |
| `flutter test` | **64 passed** (up from 19) |

## Next-slice ordering (per the user's plan)

The user's accepted ordering: **16 → 17 → 15**.

- **Slice 16** — real FCM push + delivery webhooks. Device-token
  registration endpoint, FCM service-account flow on the backend,
  Twilio/FCM webhook handlers that move
  `notification_attempts.status` to `delivered`. Closes brief §14
  DoD #2 (push AND SMS AND voice within 10 s).
- **Slice 17** — scheduler infra for the 60s no-ack backup escalation
  and the stuck-claim reaper. Resolves the remaining
  `[NEEDS_OPS_DECISION]` in `docs/open-questions.md`.
- **Slice 15** — dashboard rebuild on shadcn/ui + Tailwind + TanStack
  Query, real doctor login, component decomposition, Vitest test
  coverage on the alert→PDF path.
