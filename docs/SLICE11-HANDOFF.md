# Slice 11 Handoff (temporary — delete after Slice 11 lands)

> Purpose: everything needed to start Slice 11 (polish + elderly UX +
> disclaimer ack + connected-devices) without relying on chat-only
> context. Scope per `PLAN.md` Slice 11 and `docs/ux-copy.md` (the
> canonical-strings source of truth).

## Branch state

- All Slices 1–10 work lives on branch
  `slice-6-emergency-hardening`, **pushed** to origin at tip
  `c952d98` (`feat: add Slice 10 admin KPIs and exports`).
- Default branch (`master`/`main`) is **untouched**.
- This handoff doc sits on top of that tip; its own SHA shifts on
  amend, so the doc does **not** name itself. Branch Slice 11 from
  the current `slice-6-emergency-hardening` `HEAD` — branching from
  an earlier point loses one or more of the Slice 7–10 work commits
  (full list in `git log slice-6-emergency-hardening`).
- Suggested PR title for the combined branch when it merges:
  `Slices 7–10: case lifecycle, handover PDF, medicine reminders,
  admin KPIs`. Slice 11 work either folds into that PR or opens a
  follow-up — your call.

## Slice 11 scope (`PLAN.md` + `docs/ux-copy.md`)

> Polish — empty/error states, offline indicator, elderly UX (§11:
> 56dp targets, 18/22sp fonts, 30% red button, voice-over labels,
> `tr()` wrapping). Wire `DISCLAIMER_FULL` into onboarding as a
> required acknowledgment screen (logged to `audit_log`); render
> `DISCLAIMER_SHORT` as a sticky footer on home/vitals/records;
> ensure splash + login carry the `Call 108` `tel:108` deep link
> reachable in ≤2 taps. Add Settings → "Connected devices" screen
> with the exact Phase-2 wearables copy (no connect buttons, no
> placeholders). **No external keys required.**

### What "demoable" looks like

1. A new resident going through onboarding hits a dedicated
   **disclaimer acknowledgment screen** before consents — they cannot
   continue without tapping "I understand", and the tap is recorded
   in `audit_log` as `DISCLAIMER_ACKNOWLEDGED` (the audit action
   already exists in [enums.py:168](apps/backend/app/enums.py#L168),
   the gate already exists in
   [onboarding_service.py:67](apps/backend/app/services/onboarding_service.py#L67),
   and the audit write already exists at
   [onboarding_service.py:161](apps/backend/app/services/onboarding_service.py#L161)
   — the mobile UI must surface this as its own screen, not a
   buried checkbox).
2. Every health-insight screen (home, vitals, records) carries the
   `DISCLAIMER_SHORT` as a sticky footer — never scrolls away.
3. The splash and login screens carry a large red "Call 108"
   button that resolves the `tel:108` deep link in ≤ 2 taps from any
   entry point, even with no auth. The existing splash already wires
   `kCall108` ([main.dart:17](apps/mobile/lib/main.dart#L17)) via an
   **injectable** `UriLauncher` typedef ([main.dart:20](apps/mobile/lib/main.dart#L20))
   so widget tests assert the dial without a real dialer — keep that
   seam.
4. The mobile typography matches brief §11 / PLAN.md §11: **56 dp**
   minimum tap targets on every primary action, **18 sp** body /
   **22 sp** emphasis, and the emergency button is **red and uses
   ~30 % of the visible action area** on the home screen (already
   `Colors.red` on the splash; verify the in-app emergency surface
   matches).
5. Every user-facing string is wrapped in `tr('...')` — the
   placeholder at [main.dart:9](apps/mobile/lib/main.dart#L9) is the
   identity function until real i18n lands in a later slice;
   wrapping today is the discipline that makes the Phase-2 swap a
   one-day job.
6. Every primary action has a `Semantics` label so screen readers
   (TalkBack / VoiceOver) name the button — see the existing
   `Semantics(button: true, label: tr('Call 108'), child: …)` at
   [main.dart:125](apps/mobile/lib/main.dart#L125) as the pattern.
7. A Settings → **Connected devices** screen shows the exact Phase-2
   wearables copy (`CONNECTED_DEVICES_PHASE2` in
   [ux-copy.md:43](docs/ux-copy.md#L43)) and **nothing else** — no
   connect buttons, no fake "Coming Soon" UI, no placeholder device
   list.
8. Empty/error states are present on every list surface (resident
   records list, doctor active-alert feed, medicine schedules, doctor
   adherence view, admin KPIs panel) — a quiet helpful sentence,
   never a stack trace, never the literal `null`.
9. An **offline indicator** (Flutter side) is visible when the device
   has no connectivity, so the resident knows the in-flight alert
   retry is queued. Reuse the existing offline emergency-queue
   plumbing (`EmergencyController` + `FilePendingAlertStore` in
   [emergency_api.dart](apps/mobile/lib/emergency_api.dart)) for the
   signal — do not invent a new background ping.

### What demo proof looks like (PLAN.md row)

> Manual a11y pass + widget tests; disclaimer ack row in
> `audit_log`; `tel:108` reachable ≤2 taps from splash/login.

Translates to:

- **Widget tests** asserting the disclaimer ack screen blocks
  onboarding completion when not tapped (and the launcher fixture
  records the `tel:108` URI from BOTH splash and login).
- **Backend test** asserting `AuditAction.DISCLAIMER_ACKNOWLEDGED`
  lands in `audit_log` exactly once per onboarding completion
  (extend `test_onboarding.py`).
- **A11y assertions**: every primary button has a `Semantics(button:
  true, label: …)` ancestor in widget tests.

## Integration points (Slices 1–10) — must be honored

### 1. PHI-role isolation (Slice 2 + Slice 10) is still load-bearing

- `NON_PHI_ROLES = frozenset({BUILDER_ADMIN, SECURITY_DESK})` at
  [enums.py:22](apps/backend/app/enums.py#L22) is unchanged. The
  RBAC matrix at
  [tests/test_rbac.py](apps/backend/tests/test_rbac.py)
  (22 PHI routes × 2 PHI-blocked roles = 44 parametrized
  assertions) must stay green. UI polish must never:
  - widen `forbid_phi_roles`;
  - add a "polish-only" PHI surface (e.g. an inline patient
    snippet on the admin dashboard for context);
  - branch on `actor.role == BUILDER_ADMIN` inside any PHI
    handler.
- The Settings → Connected devices screen is **not** PHI — it's a
  static copy block. It must not display the resident's name,
  emergency contacts, or any device IDs.

### 2. Consent contracts (Slices 3 / 8 / 9) stay live-state

- `assert_consent` at
  [residents_service.py:80](apps/backend/app/services/residents_service.py#L80)
  is the single source of truth for live consent reads. Slice 11
  UI tweaks must not cache consent state on the device beyond a
  single screen render — `EMERGENCY_SHARE_WITH_HOSPITAL`,
  `EMERGENCY_SHARE_WITH_DOCTOR`, and `MEDICINE_REMINDER_NOTIFICATIONS`
  revocation must remain effective on the next backend call.
- The disclaimer ack screen is **separate** from consent. The
  `DISCLAIMER_ACKNOWLEDGED` audit action already exists. **Do not
  invent a new "disclaimer consent" row in the `consents` table** —
  the audit row is sufficient and matches `PLAN.md` Slice 11
  ("logged to `audit_log`"). The brief intentionally treats the
  disclaimer as a one-time acknowledgment, not a revocable consent.

### 3. Onboarding flow (Slice 3) is one atomic transaction

- [onboarding_service.py](apps/backend/app/services/onboarding_service.py)
  rejects `disclaimer_acknowledged=False` with `422
  disclaimer_required`, and the existing audit write is part of the
  same transaction as the user/profile/contacts/consents inserts.
  Slice 11 must keep that contract — the mobile UI surfaces the ack
  as a screen, but the backend payload field stays
  `disclaimer_acknowledged: bool` and the audit row stays inside the
  onboarding transaction. **Do not** add a separate `/api/v1/me/
  disclaimer-ack` endpoint; the ack is a step in onboarding, not a
  post-hoc action.
- `Idempotency-Key` on `POST /api/v1/onboarding/complete` is
  load-bearing for offline retry. If the mobile flow now requires
  an explicit ack tap, that tap state must be part of the
  device-side draft so a retry submits the same payload.

### 4. Emergency fallback (Slice 6) — coexist, don't replace

- The splash `Call 108` button at
  [main.dart:128](apps/mobile/lib/main.dart#L128) launches via the
  injectable `UriLauncher` typedef (existing test pattern at
  [test/widget_test.dart](apps/mobile/test/widget_test.dart)). The
  login screen must use the **same** typedef so widget tests assert
  both surfaces with one fixture.
- The 60-second fallback action sheet (Slice 6) that surfaces during
  an in-flight alert with no ack is a **different** surface from the
  splash `Call 108`. Both ship. Do not collapse them — the fallback
  sheet carries on-call doctor / 108 / 112 / family / security-desk;
  the splash is purely the always-reachable 108 entry point.
- The brief §2.2 hardcoded numbers are **108 and 112 only**. No
  other phone number is hardcoded in mobile code; the splash button
  uses `kCall108`. Don't add a hardcoded 112 button on splash —
  keep splash to the one reachable-in-≤2-taps 108 surface per
  PLAN.md.

### 5. Slice 7/8/9/10 surfaces are content-stable

- **Lifecycle (Slice 7)**: do not add new statuses, transitions,
  vitals fields, or note types. Empty/error states are display-only.
- **Handover (Slice 8)**: do not change generation / link refresh /
  dispatch / consent-gate behaviour. The dashboard's handover panel
  may gain empty/error states and a11y labels, but its API contract
  is frozen — all 16 handover tests stay green.
- **Medicine reminders (Slice 9)**: the mobile `MedicineController`
  + `LocalReminderScheduler` seam at
  [medicine.dart](apps/mobile/lib/medicine.dart) is unchanged.
  Slice 11 can wire a real `flutter_local_notifications` adapter
  behind the seam if the platform-native channels are ready — but
  this is the only place that adapter belongs. Do not bypass the
  seam to call a native plugin from the schedule screen directly.
- **Admin dashboard (Slice 10)**: aggregate-only invariant holds.
  Empty states for admin tables must not introduce per-resident
  drill-down. The 44-row PHI RBAC matrix is the contract.

### 6. Notification gateway live-mode (Slice 6 / 8) — no new push paths

- `get_notification_gateway()` returns `TwilioNotificationGateway` in
  `PROVIDER_MODE=live` for SMS / voice / WhatsApp; `send_push`
  still raises (FCM is a separate operator-keys task). Slice 11
  must NOT introduce any new server-driven push notification (e.g.
  "tap to re-acknowledge"). All Slice 11 cues are device-local
  (offline indicator, sticky footer, semantic labels) or in-line
  responses to existing endpoints.

### 7. Enum drift — still manual

- `apps/backend/app/enums.py` ↔ `packages/shared-types/src/enums.ts`
  ↔ Flutter mirror. Slice 10 retroactively mirrored the full
  `AuditAction` enum to TS — keep that. Slice 11 probably adds
  zero enums (it's UI polish), but if any are added, mirror them in
  the same commit. There is still no automated drift check.

## What Slice 11 should NOT do

- Don't add a new `consents.consent_type` for the disclaimer — use
  the existing `DISCLAIMER_ACKNOWLEDGED` audit action.
- Don't add a standalone disclaimer endpoint; keep it inside the
  onboarding transaction.
- Don't hardcode any phone number other than 108 and 112 anywhere
  in mobile code.
- Don't paraphrase `DISCLAIMER_FULL`, `DISCLAIMER_SHORT`, or
  `CONNECTED_DEVICES_PHASE2`. Reference the canonical string from
  `docs/ux-copy.md` — re-paraphrasing in components risks
  reintroducing diagnosis language (the wording history in
  ux-copy.md is real — Addition 4 originally said *"diagnose or
  treat illness"* and was reverted).
- Don't wrap `tr()` calls in conditional logic that hides them from
  a future automated grep — keep them inline `tr('literal string')`
  so the i18n key extractor can find them in Phase 2.
- Don't introduce a real i18n library yet. `tr()` stays the
  identity function in this slice; the value is the wrapping
  discipline, not the resolution.
- Don't widen `forbid_phi_roles` or add PHI to KPI/admin/Settings
  surfaces.
- Don't push to `master`/`main` (cadence per PLAN.md: implement →
  verify → commit → stop for review).

## Verification cadence (unchanged)

Per-slice verification baseline — Slice 11 must clear all of this
from its first commit forward:

- Backend
  - `ruff check .` — clean
  - `alembic upgrade head` — clean
  - `alembic check` — no model drift (Slice 11 should add no
    migrations; if it does, that's a smell — surface it in the
    review)
  - `pytest` — green; the disclaimer-ack onboarding row test is a
    new assertion in `test_onboarding.py`
- `packages/shared-types`: `npm run typecheck` — clean
- `apps/dashboard`: `npm run typecheck`, `lint`, `build` — clean
- `apps/mobile`: `flutter analyze`, `flutter test` — clean; the
  new widget tests (disclaimer ack screen, splash + login
  `tel:108`, connected-devices copy, sticky footer) push the count
  up from 19

## Baseline from current green state (Slice 10 post-push)

Captured at tip `c952d98`:

| Suite | Result |
|---|---|
| Backend `ruff` | All checks passed |
| Backend `alembic upgrade head` + `alembic check` | No model drift |
| Backend `pytest` | **152 passed** (67 emergency + 16 handover + 23 medicines + 2 admin + 44 RBAC matrix + others) |
| `shared-types` typecheck | Clean |
| Dashboard typecheck / lint / build | Clean (161 kB bundle) |
| `flutter analyze` | No issues |
| `flutter test` | **19 passed** |

Slice 11 must not regress any of these. The new headline tests are
the widget tests that prove the a11y / disclaimer / `tel:108` claims
in the PLAN.md Slice 11 row.
