# Plan: Residential Emergency Health Response Platform — MVP

## Context

Greenfield build. The repo (`github.com/kallurivenkatesh4416-commits/medical_app`) is
empty — no commits, fresh `master` branch. The goal is the Phase-1 MVP defined in the
project brief: a one-tap residential emergency alert that reaches an on-duty doctor via
push + SMS + voice, full patient context for the doctor, vitals + case lifecycle, a
Hospital Handover PDF, medicine reminders, and a PHI-free admin KPI dashboard — under
India's DPDP / Telemedicine / CDSCO constraints.

This MVP is too large to build correctly in one pass, so it will be delivered in
**demoable vertical slices**, each gated on your approval.

### Decisions locked with you (2026-05-18)

| Topic | Decision | Plan impact |
|---|---|---|
| This iteration's scope | **PLAN.md only** | This document is the only deliverable now. No code until you approve. |
| External services | **I have some keys** | All providers behind swappable gateways; real impl + dev stub chosen by env var. You supply keys when a slice needs them. |
| On-duty doctor | **On-call schedule table** | Add `on_call_schedules` table (extends §6). Fan-out + 60s backup escalation read from it. |
| Deploy target | **Local now, AWS later** | Docker-compose first-class; AWS deploy documented in `docs/setup.md` but EC2/RDS/S3 provisioning is a separate future task. |

---

## Repository structure (per §5)

```
/
├── PROJECT_BRIEF.md
├── PLAN.md
├── docker-compose.yml          # postgres + backend + localstack(S3) + mailhog
├── .env.example
├── .github/workflows/ci.yml    # lint + test + build, all 3 packages
├── docs/                       # architecture, api-contracts, data-model,
│                               # emergency-sop, compliance-notes, setup,
│                               # ux-copy, onboarding-flow, demo-script,
│                               # open-questions
├── apps/
│   ├── backend/                # FastAPI, SQLModel, Alembic
│   ├── dashboard/              # React (Vite) + TS + TanStack Query + shadcn/ui
│   └── mobile/                 # Flutter resident app
└── packages/
    └── shared-types/           # OpenAPI-generated TS types + enum source of truth
```

`shared-types` holds the canonical enums (roles, case status, symptom codes,
consent types, record types). Backend generates OpenAPI → TS types for the dashboard;
Flutter mirrors the same enums via a generated Dart file (checked in CI for drift).

---

## Data model refinements (extends §6)

Keep all §6 tables. Add / clarify:

- **`on_call_schedules`** — `id, project_id, role(doctor|nurse|ops|security_desk),
  user_id, starts_at, ends_at, is_backup(bool)`. Fan-out resolves the active primary;
  the 60s no-ack timer escalates to the active `is_backup=true` row.
- **`notification_attempts`** — `id, case_id, channel(fcm|sms|voice), recipient_id,
  status(queued|sent|delivered|failed|acknowledged), provider_ref, error, attempted_at`.
  (Brief §7 references this table; making it explicit.)
- **`case_events`** — derived, append-only state-transition log feeding KPIs and the
  PDF timeline (separate from `audit_log`, which is the compliance record).
- Every table: `id UUID`, `created_at`, `updated_at`, `project_id` where tenant-scoped.
- `audit_log` is append-only enforced at DB level (revoke UPDATE/DELETE; trigger guard).
- Soft-delete column + 30-day purge job for patient-owned data (DPDP §2.3).
- **`users.role`** — extend enum with `security_desk` (gated-community facility desk
  that reaches the flat before the doctor: unlock gates, hold lift, guide doctor).
- **`projects.enable_security_desk_alerts`** — boolean, default `false`. When `true`,
  fan-out includes the active `security_desk` on-call recipient.
- **Security-desk payload (minimum-necessary, DPDP):** when a `security_desk`
  recipient is in the fan-out, the alert payload contains **only** patient name,
  flat/villa number, emergency location text, primary contact phone, case ID. No
  symptoms, vitals, history, notes, or records — ever.
- **`consents.consent_type`** — enum with exactly these six values: `data_storage`
  (required to use the app at all; revoking triggers account-closure flow),
  `emergency_share_with_doctor`, `emergency_share_with_hospital`,
  `family_member_access`, `medicine_reminder_notifications`,
  `data_deletion_processed` (set true after a deletion request is honoured). Each row
  carries `granted_at`, `revoked_at`, `policy_version` (a policy change forces
  re-consent rather than silent acceptance). Every consent grant, revocation, and
  re-consent event writes to `audit_log`.

ER diagram + per-table notes go in `docs/data-model.md`, updated each slice.

---

## Provider abstraction (per §2.2, §13)

Two gateway interfaces in the backend, selected by env (`PROVIDER_MODE=stub|live`):

- **`NotificationGateway`**: `send_push()`, `send_sms()`, `place_voice_call()`.
  - `live`: Twilio (SMS+voice), FCM (push). Gupshup left as a documented alt impl.
  - `stub`: writes to console + `notification_attempts` so the emergency path is
    fully testable with zero external deps in dev/CI.
- **`StorageGateway`**: `put_encrypted()`, `signed_url(ttl<=15m)`.
  - `live`: S3 + SSE-KMS. `stub`: LocalStack.

Business logic depends only on the interface. Fan-out fires all three channels in
parallel; each logs independently; one failing never blocks the others.

---

## Build order (refines §9 — each step ends demoable, gated on your approval)

| # | Slice | Demo proof | Needs your keys? |
|---|---|---|---|
| 1 | **Foundation** — monorepo, docker-compose, Postgres+Alembic, FastAPI/React/Flutter skeletons, CI green, health endpoints | `docker compose up` → all services healthy, CI passes | No |
| 2 | **Auth + RBAC + audit** — OTP login (stub SMS), JWT access/refresh, role middleware, append-only `audit_log` | Login as each role; RBAC + audit unit tests pass | No |
| 3 | **Resident onboarding + consent + medical profile** — OTP-completed users walk through: demographics → emergency contacts → consent screens (one per `consent_type`, plain-English summary + "Read full policy" link, not skippable, only `data_storage` non-declinable) → medical profile. Consents persisted with `policy_version`. Onboarding screen order documented in `docs/onboarding-flow.md`. | New resident on device completes full onboarding; consent rows visible in DB; doctor sees profile; revoking a consent in Settings is reflected immediately in backend access checks | No |
| 4 | **Medical records upload** — StorageGateway (LocalStack), signed URLs ≤15m, tagging, list | Upload PDF on device, doctor opens via signed link | No |
| 5 | **Emergency happy path** — button → API (idempotent) → `emergency_case` → FCM push to on-call doctor → dashboard live feed | Tap button, doctor dashboard shows alert | FCM key |
| 6 | **Emergency hardening** — add SMS + voice (Twilio), `on_call_schedules` resolution, 60s backup escalation, mobile offline queue + retry, "Alert sent/Retrying" UI. **Failed-alert fallback UX:** 60s post-alert countdown on mobile; if no server `acknowledged` event in 60s, surface a large high-contrast fallback action sheet — Call doctor (on-call direct line) · Call 108 · Call 112 · Call primary family contact · Call security desk (last only if `enable_security_desk_alerts=true`). Original alert keeps retrying in background; countdown does not cancel it. Every fallback tap written to `case_events` with the channel chosen. Emergency fallback phone numbers must be resolved from backend project settings or the active on-call schedule, never hardcoded except 108 and 112 | All 3 channels fire; kill push, others still deliver; offline queue test. **Acknowledgment-suppressed test:** server holds back `acknowledged` for 60s → fallback sheet appears with all five buttons; tapping a button records a `case_events` row and dials the correct number on a real device | Twilio + FCM |
| 7 | **Case lifecycle** — acknowledged→en_route→on_site→treated/escalated→closed, vitals, notes (Telemedicine fields), audit + `case_events` each transition | Full lifecycle on dashboard, KPIs compute | No |
| 8 | **Hospital handover PDF** — WeasyPrint, all §8 sections, S3 + signed link, email (Mailhog dev) + WhatsApp dispatch | Generate PDF, open via 15-min link, contains every §8 field | Twilio (WhatsApp) |
| 9 | **Medicine reminders** — schedule, Flutter local notifications, taken/skipped logs, doctor visibility | Schedule reminder, log taken, doctor sees adherence | No |
| 10 | **Admin dashboard** — aggregated KPIs only, builder role PHI-blocked (automated RBAC test), monthly PDF/CSV export | builder_admin login: KPIs visible, patient drill-in blocked by test | No |
| 11 | **Polish** — empty/error states, offline indicator, elderly UX (§11: 56dp targets, 18/22sp fonts, 30% red button, voice-over labels, `tr()` wrapping). Wire `DISCLAIMER_FULL` into onboarding as a required acknowledgment screen (logged to `audit_log`); render `DISCLAIMER_SHORT` as a sticky footer on home/vitals/records; ensure splash + login carry the `Call 108` `tel:108` deep link reachable in ≤2 taps. Add Settings → "Connected devices" screen with the exact Phase-2 wearables copy (no connect buttons, no placeholders) | Manual a11y pass + widget tests; disclaimer ack row in `audit_log`; `tel:108` reachable ≤2 taps from splash/login | No |
| 12 | **Hardening** — emergency-path load test, 2G simulation, offline queue + fallback verification, ≥80% coverage on emergency path | Load + chaos test report in `docs/` | Twilio + FCM |

AWS deploy (TLS, rate limiting, WAF, EC2/RDS/S3) documented in `docs/setup.md` and
done as a **separate task** when you request it (your "local now, AWS later" choice).

---

## First 3 commits (after approval)

1. `docs: add project brief and implementation plan` — commit `PROJECT_BRIEF.md` +
   repo-root `PLAN.md`, `.gitignore`, `.env.example`. `docs/` stubs created, with
   `docs/ux-copy.md` and `docs/compliance-notes.md` **seeded** (not empty) from the
   canonical copy and `[NEEDS_LEGAL_REVIEW]` items in this plan; `docs/open-questions.md`
   created with the `[NEEDS_OPS_DECISION]` convention noted.
2. `chore: monorepo skeleton and docker-compose` — `apps/{backend,dashboard,mobile}`
   + `packages/shared-types` skeletons, `docker-compose.yml` (postgres, backend,
   localstack, mailhog), `.github/workflows/ci.yml`.
3. `feat(backend): app bootstrap with health checks and migrations` — FastAPI app,
   SQLModel + Alembic baseline, structured JSON logging, `/healthz` + `/readyz`,
   first passing pytest, CI green.

Conventional commits throughout; one slice = one PR-sized commit batch with a
description stating what it does / doesn't do / what was tested / compliance impact.

---

## Compliance handling (per §13 — flag, don't decide)

`docs/compliance-notes.md` created in commit 1 and updated every slice. Every
uncertain legal point gets a `[NEEDS_LEGAL_REVIEW]` tag, never a silent call.
Initial open items to record: DPDP cross-border S3 region, consent UX wording,
audit-log retention period, Telemedicine doctor-registration verification, WhatsApp
hospital opt-in record-keeping, data-deletion request workflow & 30-day purge legality;
plus — security-desk alert payload minimum-necessary set under the DPDP necessity
principle (confirm "name + flat + contact + case id" is acceptable); consent
versioning trigger that forces re-consent (policy-text / scope / retention change)
and its UX; native dialer fallback (`tel:` for 108 / 112 / on-call doctor) — any
regulatory implications for an app that programmatically initiates emergency calls.
No diagnosis language anywhere; no AI/ML; no wearables; builder role never sees PHI;
security desk never sees PHI beyond the minimum payload above.

Operational (non-legal) unknowns are logged separately in `docs/open-questions.md`
under a `[NEEDS_OPS_DECISION]` tag; implementation continues with a sensible
technical default and never invents business policy. The "Builder Pilot Operating
Plan" (staffing, SLAs, ambulance, pricing, liability) is the user's separate business
document — explicitly **not** in this repo, not stubbed, not committed.

### Canonical UX copy (commit 1 → `docs/ux-copy.md`)

- **`DISCLAIMER_FULL`** (once during onboarding, required acknowledgment): "This app
  does not diagnose or treat illness. It alerts qualified medical staff, records
  health information, and supports emergency coordination. Final clinical decisions
  remain with the registered doctor. In a life-threatening situation, call 108 or
  112 immediately."
- **`DISCLAIMER_SHORT`** (footer on home/vitals/records): "Not a diagnostic tool.
  Call 108 in a life-threatening emergency."
- **`OFFLINE_EMERGENCY_BUTTON`** (splash/login, always visible): "Call 108"
- **Connected-devices copy** (Settings, Slice 11): "Wearable integration (Google
  Health Connect on Android, Apple HealthKit on iOS) is planned for Phase 2. The MVP
  supports manual vitals entry by clinical staff." (Same line in `docs/demo-script.md`.)

---

## Verification (how each slice is proven)

- **Backend:** pytest unit + integration; ≥80% coverage enforced on the emergency
  path; explicit tests for notification fan-out (one channel down → others fire),
  RBAC (builder_admin PHI block), idempotency, PDF field completeness.
- **Security-desk RBAC test:** a logged-in `security_desk` user can read the
  active-alert summary for their project but receives `403` on any patient profile,
  medical record, vitals, notes, or PDF endpoint. Mirrors the `builder_admin` test.
- **Export-isolation test:** `builder_admin` and `security_desk` roles cannot export
  patient-level data through PDF, CSV, API, or signed links.
- **Dashboard:** Vitest + React Testing Library on alert feed, case view, admin gate.
- **Flutter:** widget tests for emergency button (one tap, 5s cancel countdown) and
  profile screens.
- **End-to-end demo (§14 DoD):** scripted run via docker-compose proving the full
  story — register → profile → upload → tap → 3-channel alert → doctor lifecycle →
  PDF via 15-min signed link → builder RBAC block — with the offline "Call 108"
  button reachable in ≤2 taps from splash/login.
- CI (lint + test + build, all 3 packages) must be green before any slice is "done".

---

## What this iteration delivers

Delivered in demoable vertical slices. Commits 1–3 (Slice 1 Foundation) land first,
then a stop for review before Slice 2.
