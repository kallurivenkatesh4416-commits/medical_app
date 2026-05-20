# Slice 10 Handoff (temporary — delete after Slice 10 lands)

> Purpose: everything needed to start Slice 10 (admin dashboard +
> monthly export) without relying on chat-only context. Scope per
> `PLAN.md` Slice 10.

## Branch state

- All Slice 7 / 8 / 9 work lives **only** on branch
  `slice-6-emergency-hardening`.
- **Tip = current `HEAD`** on that branch. This handoff doc itself sits
  on top of the Slice 9 review #3 fix `3d8af81` and is the most recent
  commit; an amend changes its own SHA, so this doc deliberately does
  **not** name its own SHA — use `git rev-parse HEAD` for the exact
  value at branch time.
- The branch is **ahead of `origin/slice-6-emergency-hardening` by 10
  commits and is NOT pushed**. Branch Slice 10 from the current
  `slice-6-emergency-hardening` `HEAD` — branching from an earlier
  point loses one or more of:
  - `5585ba1` — Slice 7 feat (case lifecycle)
  - `4117a22` — Slice 8 feat (handover PDF, xhtml2pdf, email + WhatsApp)
  - `a8541d8` — Slice 8 review fixes (hospital consent, live signed
    link, live Twilio, durable dispatch outbox)
  - `77eff14` — Slice 8 env/setup docs follow-up
  - `f704d48` — Slice 9 feat (medicine reminders + handover §6 wire-in)
  - `2868cb3` — Slice 9 review fixes (reminder-consent revoke, slot
    integrity, owner-checked deactivate, tighter validation)
  - `928a98e` — Slice 9 review #2 fix (future-dated dose logs)
  - `3d8af81` — Slice 9 review #3 fix (tz-aware `scheduled_for`)
  - this Slice 10 handoff doc itself (its SHA is whatever `HEAD`
    resolves to when you read this)

## Slice 10 scope (`PLAN.md`)

> Admin dashboard — aggregated KPIs only, builder role PHI-blocked
> (automated RBAC test), monthly PDF/CSV export. Demo proof:
> `builder_admin` login → KPIs visible, patient drill-in blocked by
> test. **No external keys required.**

What "demoable" looks like end-to-end:

1. A `builder_admin` (project-scoped) logs in via the existing OTP
   flow.
2. They land on the dashboard with **aggregate-only** KPIs across
   prior slices: emergency cases, ack/on-site response times (already
   in Slice 7's `GET /api/v1/emergency/kpis` — Slice 10 expands the
   surface, not the precedent), residents onboarded, medical records
   uploaded, medicine adherence at project level.
3. Every attempt to drill into a specific resident / case / record is
   blocked by the existing `forbid_phi_roles` dep — and the
   automated RBAC matrix in `tests/test_rbac.py` extends to cover
   **every** PHI route added through Slice 9.
4. They can download a **monthly aggregate** PDF/CSV (no PHI), built
   from the same KPIs over a configurable month, via a new admin
   export endpoint.

## Integration points (Slices 1–9) — must be honored

### 1. PHI-role isolation is the load-bearing invariant

- `NON_PHI_ROLES = frozenset({BUILDER_ADMIN, SECURITY_DESK})` in
  [enums.py:22](apps/backend/app/enums.py#L22) — **do not add a third**.
  The whole Slice 10 surface for `builder_admin` rides on this set
  staying tight.
- `forbid_phi_roles` ([deps.py:84](apps/backend/app/security/deps.py#L84))
  is the single chokepoint. Every PHI endpoint added through Slice 9
  uses `dependencies=[Depends(forbid_phi_roles)]` (see how
  [emergency.py](apps/backend/app/api/emergency.py),
  [handover.py](apps/backend/app/api/handover.py), and
  [medicines.py](apps/backend/app/api/medicines.py) wire it). Slice 10
  must NOT:
  - widen this dep to allow `builder_admin` for any existing route;
  - add an `if actor.role == BUILDER_ADMIN: return less_data()` branch
    on a PHI route (always a separate aggregate route);
  - add a new PHI surface that forgets the dep.
- The automated RBAC test ([test_rbac.py:64](apps/backend/tests/test_rbac.py#L64))
  currently uses one synthetic `/_t/phi` route. Slice 10 should
  **extend the matrix** to enumerate every real PHI endpoint
  shipped through Slice 9 and assert `builder_admin` (and
  `security_desk`) hit 403 on each. PLAN row says
  "automated RBAC test" — the matrix is the deliverable.

### 2. Aggregate KPI precedent — extend, don't duplicate

- `GET /api/v1/emergency/kpis` already exists (Slice 7,
  [emergency.py:22](apps/backend/app/api/emergency.py#L22) —
  `kpi_roles = require_roles(DOCTOR, NURSE, OPS, BUILDER_ADMIN)`).
  Output is `total_cases / active_cases / closed_cases /
  average_ack_seconds / average_on_site_seconds` — all integers and
  durations, **no resident identifiers**.
- Slice 10 should add an `/api/v1/admin/kpis` (or similar) that
  composes the same shape for the other surfaces:
  - residents onboarded over a window
  - medical records uploaded over a window
  - medicine adherence at project level — reuse the live `missed`
    computation from [medicines_service.adherence_summary_for_staff](apps/backend/app/services/medicines_service.py)
    aggregated across residents (NOT per-resident drill-in)
  - emergency KPI fields already in `emergency_kpis`
- The PRN exception documented in `compliance-notes.md` ("Slice 9
  implementation notes") still applies — adherence rows from
  `as_needed` schedules contribute to `taken` but not to
  `scheduled_slots`. Project-level aggregate must not divide by
  `scheduled_slots == 0`. Round, clamp, or expose them as separate
  series — pick one and lock it in tests.

### 3. Monthly PDF/CSV export — reuse Slice 8 pipeline, write no PHI

- The handover PDF (`handover_service._render_pdf` in
  [handover_service.py](apps/backend/app/services/handover_service.py))
  uses Jinja2 → xhtml2pdf — that renderer is reusable for the
  monthly export. Put the template under
  `apps/backend/app/services/admin_templates/monthly.html.j2` (new
  folder; same packaging story as `handover_templates/`).
- CSV uses Python stdlib `csv` — no new dep. Stream via
  `fastapi.responses.StreamingResponse` or build the body in-memory
  and return as `text/csv`.
- The export is aggregate-only. **Do not** route through
  `StorageGateway` (the Slice 4/8 PHI surface). Stream the response
  directly with `application/pdf` or `text/csv`; no signed-link token
  is needed because there is no PHI to gate. This is the only
  intentional asymmetry with the Slice 8 handover flow — handover is
  PHI + storage + signed link; admin export is non-PHI + direct stream.
- Honour the project tenant scope — the actor's `project_id` filters
  every aggregate query. The whole export must reflect one project,
  not the platform.
- Audit every export with a new `AuditAction.ADMIN_EXPORT_GENERATED`
  (mirror to `shared-types/enums.ts` in the same commit — manual sync
  per integration point #6).
- The brief / `docs/compliance-notes.md` "Slice 7 implementation
  notes" already states `builder_admin` can read aggregate KPIs but
  cannot read patient-level case detail, vitals, or notes. Reaffirm
  this in the Slice 10 compliance entry — and make the matrix test
  enforce it.

### 4. Hospital handover (Slice 8) behaviour is unchanged

- `EMERGENCY_SHARE_WITH_HOSPITAL` consent gate, the 15-min signed
  link, the stub-vs-live URL shape (backend-proxied vs S3 presigned),
  the `handover_url` JWT type distinct from `record_url`, the durable
  email/WhatsApp dispatch outbox — **do not rework any of this**. The
  16 handover tests remain the contract.
- The handover PDF §6 medicines wire-in (Slice 9) likewise stays.
  Slice 10's admin export is a **separate** PDF pipeline — do not
  fold admin metrics into the §8 template or vice versa.

### 5. Medicine reminders (Slice 9) contracts that must hold

- `MEDICINE_REMINDER_NOTIFICATIONS` live-state read at
  [medicines_service.py](apps/backend/app/services/medicines_service.py)
  `list_own_schedules`: revoke → empty list → mobile cancels reminders.
  Slice 10's adherence aggregate must **not** read schedules of
  residents who have revoked this consent into the project totals.
  Either filter at query time, or treat their schedules as "consent
  paused" and exclude from the slot projection. Either way, document
  it.
- `EMERGENCY_SHARE_WITH_DOCTOR` is the gate for **per-resident**
  adherence reads (staff PHI surface). The Slice 10 **aggregate** read
  does not gate on per-resident consent — it's project-level
  non-PHI — but it must inherit Slice 9's UTC-naive convention,
  slot-integrity invariant, and PRN exception. See
  [medicines_service.py](apps/backend/app/services/medicines_service.py)
  `adherence_summary_for_staff` for the slot projection that already
  handles the invariant `taken + skipped ≤ scheduled_slots`.
- Dose-log slot integrity, future-dose guard, and the tz-aware
  `_to_naive_utc` normalisation (Slice 9 review rounds #1–#3) are all
  inside `log_own_dose`. Slice 10's aggregate read paths must not
  bypass these by reading the model directly with arbitrary date
  filters — use `adherence_summary_for_staff` or its window helper.

### 6. Notification gateway live-mode assumptions (post-Slice 8 Twilio)

- `get_notification_gateway()` returns `TwilioNotificationGateway` in
  `PROVIDER_MODE=live` (SMS / voice / WhatsApp). `send_push` (FCM)
  still **raises** — Slice 10 must not add any new push-driven
  notification path. The admin dashboard surface is request/response
  only; no broadcasts.

### 7. Enum drift — still manual across Python ↔ TypeScript ↔ Dart

- Slice 9 added `MedicineFrequency`, `MedicineDoseStatus`, and 5
  `AuditAction`s mirrored to `shared-types/enums.ts`. Slice 10
  additions (likely `AuditAction.ADMIN_EXPORT_GENERATED`,
  `AuditAction.ADMIN_KPI_READ`) follow the same discipline:
  update `apps/backend/app/enums.py` AND
  `packages/shared-types/src/enums.ts` in the same commit. There is
  still no automated drift check — that is its own future slice.

## What Slice 10 should NOT do

- Don't widen `forbid_phi_roles` or rewire `NON_PHI_ROLES`.
- Don't reuse a Slice 7/8/9 PHI endpoint with a `builder_admin`
  carve-out — always a separate aggregate route.
- Don't write resident names, flat/villa numbers, case IDs, or
  medicine names into any KPI response or monthly export — counts and
  durations only.
- Don't fold admin metrics into the §8 handover PDF, and don't fold
  handover or medicine details into the monthly export.
- Don't introduce a new PHI surface (e.g. a "resident-level admin
  drill-down for ops" — that's Slice 11/12 territory if at all).
- Don't push to `master`/`main` (cadence per PLAN.md: implement →
  verify → commit → stop for review).

## Verification cadence (unchanged)

Per-slice verification baseline — Slice 10 must clear all of this
from its first commit forward:

- Backend
  - `ruff check .` — clean
  - `alembic upgrade head` — clean
  - `alembic check` — no model drift
  - `pytest` — green, including the **extended RBAC matrix**
- `packages/shared-types`: `npm run typecheck` — clean
- `apps/dashboard`: `npm run typecheck`, `lint`, `build` — clean
- `apps/mobile`: `flutter analyze`, `flutter test` — clean (Slice 10
  has no mobile surface, but the suite must not regress)

## Baseline from current green state (Slice 9 review #3 post-commit)

Captured at tip `3d8af81`:

| Suite | Result |
|---|---|
| Backend `ruff` | All checks passed |
| Backend `alembic upgrade head` + `alembic check` | No model drift |
| Backend `pytest` | **106 passed** (67 emergency + 16 handover + 23 medicines + others) |
| `shared-types` typecheck | Clean |
| Dashboard typecheck / lint / build | Clean (158 kB bundle) |
| `flutter analyze` | No issues |
| `flutter test` | 19 passed |

Slice 10 must not regress any of these. The extended RBAC matrix is
the new headline test — every PHI route added through Slice 9 must
return 403 to a `builder_admin` and `security_desk` token.
