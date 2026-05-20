# Slice 9 Handoff (temporary — delete after Slice 9 lands)

> Purpose: everything needed to start Slice 9 (medicine reminders) without
> relying on chat-only context. Scope per `PLAN.md` Slice 9.

## Branch state

- All Slice 7 + Slice 8 work lives **only** on branch
  `slice-6-emergency-hardening`.
- **Tip = current `HEAD`** on that branch. This handoff doc itself sits
  on top of the Slice 8 follow-up `77eff14` and is the most recent
  commit; an amend changes its own SHA, so this doc deliberately does
  **not** name its own SHA — use `git rev-parse HEAD` for the exact
  value at branch time.
- The branch is **ahead of `origin/slice-6-emergency-hardening` by 5
  commits and is NOT pushed**. Branch Slice 9 from the current
  `slice-6-emergency-hardening` `HEAD` — branching from an earlier
  point loses one or more of:
  - `5585ba1` — Slice 7 feat (case lifecycle)
  - `4117a22` — Slice 8 feat (handover PDF, xhtml2pdf, email + WhatsApp)
  - `a8541d8` — Slice 8 review fixes (hospital consent gate, live
    signed link, live Twilio, durable dispatch outbox)
  - `77eff14` — Slice 8 env/setup docs follow-up
  - this Slice 9 handoff doc itself (its SHA is whatever `HEAD`
    resolves to when you read this)

## Slice 9 scope (`PLAN.md`)

> Medicine reminders — schedule, Flutter local notifications, taken/skipped
> logs, doctor visibility. Demo proof: schedule reminder, log taken, doctor
> sees adherence. **No external keys required.**

What "demoable" looks like end-to-end:

1. A resident (or doctor on behalf of a resident) creates a medicine
   schedule (drug name, dose, times-per-day, time-of-day slots,
   start/end dates).
2. The mobile app surfaces each upcoming dose as a **device-local**
   notification (Flutter `flutter_local_notifications`-style; **no FCM
   required**) — see integration point #4.
3. Tapping the notification (or the in-app medicine list) records a
   `taken` or `skipped` log entry against that scheduled dose.
4. A doctor's view in the dashboard shows the resident's recent
   adherence — counts + a per-medicine streak / missed-dose list.
5. The Slice 8 handover PDF §6 "Current Medicines: Full list" section
   (currently a placeholder) is populated from the new schedule rows —
   see integration point #3.

## Integration points (Slices 7 + 8) — must be honored

### 1. Lifecycle / RBAC / tenant isolation stay intact

- Reuse the established staff-resolver pattern: tenant filter on
  `project_id`, 404-no-existence-leak for cross-project, doctor-only
  for outcome writes. See `_case_for_staff(..., lock=lock)` in
  `apps/backend/app/services/emergency_service.py` and the
  doctor-only check in `handover_service.generate_handover`.
- Resident-owned reads use the existing `get_resident_for_user` +
  identity match (see `emergency_service.case_status_for_owner`).
- Staff-PHI roles allowed for medicines: **DOCTOR, NURSE, OPS**
  (and the resident themselves for self-view). Slice 7 lifecycle uses
  the same shape.
- Doctor-only mutations (e.g. "add prescription on behalf of resident",
  if Slice 9 supports it) should mirror the Slice 7 doctor-only outcome
  gate, not invent a new check.

### 2. No PHI to `builder_admin` / `security_desk`

- `NON_PHI_ROLES = frozenset({BUILDER_ADMIN, SECURITY_DESK})` in
  `apps/backend/app/enums.py:22` is load-bearing. Every PHI-surface API
  route must add `Depends(forbid_phi_roles)` (see how Slice 7 case
  detail, vitals, notes, handover endpoints do it).
- Aggregate-only KPIs (Slice 7 / Slice 10 pattern) can include
  `builder_admin`. Adherence "Resident A took 4/5 doses last week" is
  PHI; aggregate "this project had X% adherence" is not. Keep the line
  sharp.

### 3. Hospital handover PDF (Slice 8) is unchanged — but populate §6

- `handover_service.generate_handover` and the dispatch / link routes
  are settled; do **not** rework them.
- The Jinja2 template at
  `apps/backend/app/services/handover_templates/handover.html.j2`
  already renders a "6. Current Medicines" section that today reads
  "No current medicines on file. (Populated when the medicine-reminders
  slice lands.)" — see `_aggregate(...)` in
  `apps/backend/app/services/handover_service.py` where `"medicines":
  []` is set with a comment pointing at Slice 9.
- Slice 9 wires the real query into that `_aggregate` call; the
  template iterates `medicines` with `m.name`, `m.dose`, `m.schedule`.
  Keep those field names (or update both at once). A handover test
  should be added that asserts the new section renders for a resident
  with at least one active medicine.
- The **EMERGENCY_SHARE_WITH_HOSPITAL** consent gate on the handover
  flow stays — do not let any new medicine-related code bypass it.

### 4. Notification gateway live-mode assumptions (post-Twilio work)

- `get_notification_gateway()` now returns
  `TwilioNotificationGateway` in `PROVIDER_MODE=live` (Slice 8 review
  fix). That gateway implements `send_sms`, `place_voice_call`,
  `send_whatsapp` against Twilio REST. `send_push` (FCM) still
  **raises `NotImplementedError`** — FCM live wiring is a separate
  operator-keys task.
- Slice 9's reminders must be **device-local** (Flutter
  `flutter_local_notifications` or equivalent). Do not introduce a
  server-side FCM dispatch loop just for reminders; in live mode it
  would degrade to a permanent failure, and Slice 9's PLAN row
  explicitly says "no external keys required."
- If a future cross-device sync (e.g. "remind the family caregiver
  when the resident misses a dose") is needed, gate it behind a flag
  and document that it requires the separate FCM wiring task.

### 5. Enum drift — Python ↔ TypeScript ↔ Dart

- There is **still no automated enum drift check** (deferred to a
  later slice — same caveat as Slice 7 handoff). Update both files in
  the same commit if Slice 9 adds enums:
  - `apps/backend/app/enums.py`
  - `packages/shared-types/src/enums.ts`
  - Flutter side — copy the new enum values into the mirror used by
    the mobile app (the existing pattern is hand-mirrored).
- Likely Slice 9 additions:
  - `MedicineFrequency` (e.g. `once_daily`, `twice_daily`,
    `as_needed`) — pick a stable wire vocabulary up front; DB values
    are forever.
  - `MedicineDoseStatus` (e.g. `scheduled`, `taken`, `skipped`,
    `missed`). The "missed" vs "skipped" distinction is the
    auto-marked-after-window vs user-tapped-skip split; settle it
    before writing the migration.
  - `AuditAction` additions: `MEDICINE_SCHEDULE_CREATED`,
    `MEDICINE_DOSE_LOGGED`, `MEDICINE_ADHERENCE_READ` etc.
- The `MEDICINE_REMINDER_NOTIFICATIONS` consent already exists in
  `ConsentType` (Slice 3); the resident granted/declined it during
  onboarding. **Gate notification scheduling on it via the live
  `assert_consent` helper in `residents_service.py:80`** — same
  pattern Slice 8 review used for `EMERGENCY_SHARE_WITH_HOSPITAL`. A
  revocation must stop new notifications on the very next read.

## What Slice 9 should NOT do

- Don't repurpose `case_events` for medicine timeline entries —
  `case_events` is scoped to emergency cases. Use a new
  `medicine_dose_logs` table (or similar) with its own audit hook.
- Don't fold medicine-rendered text into the handover Telemedicine
  note fields — those have a different legal meaning (Slice 7 §2.4).
  The handover §6 medicines list is a separate section.
- Don't push to `master`/`main` (cadence per PLAN.md: implement →
  verify → commit → stop for review).

## Verification cadence (unchanged)

Per-slice verification baseline — Slice 9 must clear all of this from
its first commit forward:

- Backend
  - `ruff check .` — clean
  - `alembic upgrade head` — clean
  - `alembic check` — no model drift (the most common drift trap is
    forgetting an `op.add_column` for a new field)
  - `pytest` — green
- `packages/shared-types`: `npm run typecheck` — clean
- `apps/dashboard`: `npm run typecheck`, `lint`, `build` — clean
- `apps/mobile`: `flutter analyze`, `flutter test` — clean

## Baseline from current green state (Slice 8 review #2 post-commit)

Captured at tip `77eff14`:

| Suite | Result |
|---|---|
| Backend `ruff` | All checks passed |
| Backend `alembic upgrade head` + `alembic check` | No model drift |
| Backend `pytest` | **83 passed** (67 Slice 7 + 16 handover) |
| `shared-types` typecheck | Clean |
| Dashboard typecheck / lint / build | Clean (155 kB bundle) |
| `flutter analyze` | No issues |
| `flutter test` | 13 passed |

Slice 9 must not regress any of these — including the existing 13
Flutter widget tests when the new reminder UI lands.
