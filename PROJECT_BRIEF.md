# Claude Code Prompt: Residential Emergency Health Response Platform

> **How to use this:** Save this file as `PROJECT_BRIEF.md` in an empty repo. Open Claude Code in that folder. Start with: *"Read PROJECT_BRIEF.md fully, then ask me clarifying questions before writing any code."*

---

## 0. Before You Write Any Code

Read this entire document. Then:

1. List the **5 most important clarifying questions** you have before writing any code. Ask them. Wait for answers.
2. Once I answer, produce a `PLAN.md` with your proposed file structure, build order, and the first 3 commits you will make. Wait for my approval.
3. Only then start coding.

Do not skip steps 1 and 2. If you skip them, I will reject the work.

---

## 1. Mission

Build the **MVP** (Phase 1 only) of a Residential Emergency Health Response Platform.

**One-line product definition:**
A residential emergency health app that lets a resident or family member alert an in-station doctor instantly, share medical history, monitor basic health data, upload records, manage medicine reminders, and hand over the complete patient context to a partner hospital during escalation.

**The demo story this MVP must deliver, end-to-end:**
> A resident taps one button → the in-station doctor is alerted within seconds via push + SMS + call fallback → the doctor opens the patient's full medical profile and history → a nurse records vitals → if needed, a Hospital Handover PDF is generated and shared with the partner hospital before the patient arrives.

If your code cannot demo this story, the MVP is not done.

---

## 2. CRITICAL Safety & Legal Constraints (Read Twice)

This is a healthcare app in India. The following are **non-negotiable**:

### 2.1 Language and claims

- **NEVER** let the app output diagnostic claims like "Heart attack detected", "Stroke detected", "You have X disease."
- Use phrasing like: `"Abnormal reading detected. Doctor review required."`
- Every screen that shows health insights must include a visible disclaimer: *"This app does not replace emergency hospital care. In a life-threatening situation, call 108 / 112 immediately."*
- The emergency call number must be visible on the home screen even when offline / not logged in.

### 2.2 Emergency reliability

- The "I Need Medical Help" button **must not depend solely on push notifications**. Every emergency alert must trigger **three parallel channels**: FCM push, SMS, and a voice call to the on-duty doctor's phone. Implement all three. If any one fails, the others must still fire. Log every attempt.
- The emergency button must work even with poor connectivity. Queue the alert locally and retry. Show the user a visible "Alert sent / Retrying" status.
- The native dialer fallback (`tel:108`) must be reachable in **≤ 2 taps from any screen**, including the splash screen and the login screen.

### 2.3 Data protection (India DPDP Act 2023)

- **Role-based access is mandatory.** A builder/society admin must **never** see individual medical records. Their dashboard shows only aggregated operational KPIs (response time, case counts, etc.).
- Every read/write of a patient record must be written to an immutable `audit_log` table with `who, what, when, from_ip, purpose`.
- Consent is explicit and granular: separate toggles for (a) emergency sharing with hospital, (b) family member access, (c) wearable data collection.
- Patient must be able to request data deletion. Implement a soft-delete + 30-day purge job.
- All medical files in S3 must be encrypted (SSE-S3 minimum, SSE-KMS preferred) and accessed only via signed URLs with ≤ 15-minute TTL.

### 2.4 Telemedicine compliance

If a doctor records consultation notes through the app, the record must capture: doctor name, registration number, consultation timestamp, advice given, patient consent flag. Build the schema for this from day one even if the UI is minimal.

### 2.5 Medical-device-software boundary (CDSCO)

The MVP must **not** do automated diagnosis or treatment recommendations. It records vitals, alerts doctors, stores records, and coordinates care. That is the scope. AI risk scoring is **Phase 5, not Phase 1.** Do not build it now.

---

## 3. Scope: What's IN and What's OUT for MVP

### IN (Phase 1 only)

**Resident mobile app:**
- OTP login (mobile number)
- Profile creation (demographics, blood group, emergency contacts)
- Medical history (diseases, allergies, current medicines, surgeries, preferred hospital)
- Medical record upload (PDF/image with tag + date + source)
- One-tap "I Need Medical Help" emergency button with symptom selector
- Manual medicine reminders (schedule, taken/skipped logging)
- Visible offline emergency number

**Doctor dashboard (web + responsive):**
- Login with role check
- Live emergency alert feed (with sound + visual alert)
- Patient profile view (history, allergies, medicines, records, family contacts)
- Vitals entry (BP, SpO₂, pulse, temperature, RR)
- Case status updates: `acknowledged → en_route → on_site → treated_on_site | escalated → closed`
- Generate Hospital Handover PDF (downloadable + shareable signed link)
- Add consultation notes (with Telemedicine fields)

**Admin dashboard (web):**
- Residents onboarded count
- Active alerts (live)
- Average response time (alert → doctor acknowledged, alert → on-site)
- Cases handled / escalated
- Monthly report export (PDF/CSV) — **operational metrics only, no PHI**

**Backend:**
- All APIs above
- Notification service (FCM + SMS via Twilio/Gupshup + voice call)
- File storage (S3 with encryption + signed URLs)
- PDF generation service (handover summary)
- Audit log
- Role-based access middleware
- Health check endpoints

### OUT (do not build now)

- Wearable integration (Health Connect / HealthKit) — Phase 3
- ABDM / ABHA / FHIR integration — Phase 4
- AI risk scoring, fall prediction, readmission risk — Phase 5
- Insurance integration
- OCR on uploaded prescriptions
- Multi-language UI (English-only for MVP; structure strings for i18n)
- Ambulance partner module (Phase 2)
- Nurse-dedicated app (Phase 2 — nurses use the doctor dashboard with reduced permissions for MVP)

If you find yourself building something OUT of scope, stop and ask.

---

## 4. Tech Stack (Fixed — Do Not Substitute Without Asking)

| Layer | Choice |
|---|---|
| Mobile app | **Flutter** (latest stable). One codebase for Android + iOS. |
| Doctor/Admin dashboard | **React** (Vite) + TypeScript + TanStack Query + shadcn/ui + Tailwind |
| Backend | **FastAPI** (Python 3.11+), SQLModel/SQLAlchemy 2.x, Alembic for migrations |
| Database | **PostgreSQL 15+** |
| File storage | **AWS S3** (with KMS encryption) |
| Push notifications | **Firebase Cloud Messaging** |
| SMS / Voice | **Twilio** (or Gupshup as fallback; abstract behind an interface so it can be swapped) |
| PDF generation | **WeasyPrint** (HTML → PDF in FastAPI) |
| Hosting | AWS EC2 for MVP, containerised (Docker) so it can move to ECS later |
| Auth | OTP login + JWT (short-lived access + refresh) + role-based access control (RBAC) |
| Audit logs | Separate `audit_log` table, append-only, no UPDATE/DELETE allowed at DB level |
| Local dev | Docker Compose (Postgres + backend + LocalStack for S3 + Mailhog if needed) |
| CI | GitHub Actions: lint + tests + build on every PR |

---

## 5. Repository Structure

Create a **monorepo** with three packages:

```
/
├── PROJECT_BRIEF.md          (this file)
├── PLAN.md                   (you create this in step 0)
├── docker-compose.yml
├── .github/workflows/ci.yml
├── docs/
│   ├── architecture.md
│   ├── emergency-sop.md
│   ├── api-contracts.md
│   ├── data-model.md
│   └── compliance-notes.md
├── apps/
│   ├── mobile/               (Flutter — resident app)
│   ├── dashboard/            (React — doctor + admin)
│   └── backend/              (FastAPI)
└── packages/
    └── shared-types/         (OpenAPI-generated types shared between dashboard and mobile)
```

Use **conventional commits** (`feat:`, `fix:`, `chore:`, `docs:`, etc.). One feature = one PR-sized commit batch.

---

## 6. Core Data Model (Starting Point — Refine in PLAN.md)

Tables (PostgreSQL, snake_case, all with `id UUID`, `created_at`, `updated_at`):

- `users` — auth + role (`resident | family | doctor | nurse | ops | hospital | builder_admin | super_admin`)
- `residents` — extends users with flat number, project ID
- `family_links` — resident ↔ family member with permission flags
- `medical_profiles` — 1:1 with resident: blood group, diseases (JSONB), allergies (JSONB), surgeries, preferred hospital, insurance
- `medicines` — current medicines per resident: name, dose, frequency, start date
- `medicine_logs` — taken/skipped events
- `medical_records` — uploaded files: S3 key, type (prescription/lab/scan/discharge), date, source, tags
- `emergency_cases` — alert_time, acknowledged_at, on_site_at, closed_at, symptom_codes (array), status, location_text, resolved_outcome
- `case_vitals` — case_id, BP, SpO₂, HR, RR, temp, recorded_by, recorded_at
- `case_notes` — case_id, author_id, body, type (`observation | treatment | escalation_reason`)
- `case_escalations` — case_id, hospital_id, handover_pdf_s3_key, sent_at
- `partner_hospitals` — name, contact, address, lat/lng
- `projects` — builder/society project (multi-tenant root)
- `consents` — resident_id, consent_type, granted_at, revoked_at
- `audit_log` — append-only

Schema must support **multi-tenancy by project** from day one (so the same backend can serve multiple builder projects).

---

## 7. Emergency Workflow (Implement Exactly This)

```
1. Resident taps "I Need Medical Help"
   → app captures: resident_id, optional symptom code, GPS (if granted), timestamp
   → POST /api/v1/emergency/alerts (idempotency key = client UUID)
   → on success: show "Doctor alerted" + case ID
   → on network failure: queue locally, retry every 5s, show "Retrying..."

2. Backend creates emergency_case (status = "alerted")
   → fans out to notification service:
     a) FCM push to on-duty doctor(s) + nurse + ops + registered family
     b) SMS to all of the above
     c) Voice call to on-duty doctor (Twilio TwiML: "Emergency alert for [name] at [flat]. Press 1 to acknowledge.")
   → each channel logs delivery status to notification_attempts table
   → if no doctor acknowledges within 60s, auto-escalate to backup doctor

3. Doctor opens dashboard → sees red banner → clicks case
   → status = "acknowledged", acknowledged_at recorded
   → views patient profile (history, meds, allergies, records, family contacts)

4. Doctor / nurse arrives on site
   → marks "On site" (status = "on_site", on_site_at recorded)
   → enters vitals
   → adds observation note

5. Doctor decides outcome:
   a) Treated on site → status = "treated_on_site" → close case
   b) Escalate to hospital → status = "escalated"
      → select partner hospital
      → system generates Hospital Handover PDF
      → PDF sent via: (i) signed S3 link (15-min TTL), (ii) WhatsApp if hospital has opted in, (iii) email
      → family notified
   c) Observe → status stays open, schedule follow-up

6. Case closure
   → status = "closed", closed_at recorded
   → outcome captured
   → response-time KPIs computed in materialized view (refresh hourly)
```

Every state transition writes an entry to `audit_log` and `case_events` (a derived table).

---

## 8. Hospital Handover PDF — Required Sections

Generate a 1-2 page PDF with this exact structure. Use a clean medical-form layout (not flashy).

1. **Header:** Project name, case ID, generation timestamp, "CONFIDENTIAL — PATIENT HANDOVER"
2. **Patient Details:** Name, age, gender, blood group, flat number
3. **Emergency Complaint:** Symptom(s) selected by resident + doctor's refined assessment
4. **Timeline:** Alert raised at HH:MM, doctor acknowledged at HH:MM, on-site at HH:MM, escalated at HH:MM
5. **Vitals:** Latest readings with timestamp, plus a sparkline if multiple readings
6. **Medical History:** Diseases, allergies, surgeries
7. **Current Medicines:** Full list
8. **Doctor's Observation:** Free-text from `case_notes`
9. **Treatment Given On Site:** Oxygen / first aid / medication
10. **Reason for Escalation:** Doctor's free text
11. **Family Contact:** Primary contact name + number
12. **Attached Records:** List of recent uploaded records with signed-link references
13. **Footer:** Doctor name, registration number, signature line, hospital destination

---

## 9. Build Order (Suggested — Refine in PLAN.md)

Each step ends with a working, demoable slice. **Do not move to the next step until the current one is demoable.**

1. **Foundation:** Repo skeleton, Docker Compose, Postgres + Alembic, FastAPI hello world, React app skeleton, Flutter app skeleton, CI pipeline running.
2. **Auth:** OTP login (use a stub SMS provider in dev), JWT issuance, role-based middleware, audit log table.
3. **Resident profile + medical profile:** CRUD endpoints + Flutter screens + dashboard view.
4. **Medical records upload:** S3 (LocalStack in dev) with signed URLs, file tagging, list view.
5. **Emergency alert (happy path only first):** Button → API → DB record → FCM push to one doctor → doctor sees alert in dashboard.
6. **Emergency alert hardening:** Add SMS and voice call fallback. Add idempotency. Add offline queue on mobile. Test all three channels.
7. **Case lifecycle:** Acknowledge → on-site → vitals → notes → close. Audit log every transition.
8. **Hospital handover PDF:** Generate, store in S3, signed share link, email + WhatsApp dispatch.
9. **Medicine reminders:** Schedule, local notifications on device, taken/skipped logging, doctor visibility.
10. **Admin dashboard:** Aggregated KPIs only. No PHI for builder role. Monthly export.
11. **Polish:** Empty states, error states, offline indicator, large fonts for elderly users, accessibility (semantic labels, screen reader support).
12. **Hardening:** Load test the emergency path. Verify it works on 2G. Verify offline queue. Verify backup channels fire when push fails.

---

## 10. Code Quality Standards

- **Type everything.** TypeScript strict in dashboard. Pydantic models everywhere in backend. Dart null-safety in Flutter.
- **No magic strings.** Roles, case statuses, symptom codes, etc. live in enums shared via `packages/shared-types`.
- **Idempotency:** All write APIs that create resources accept an `Idempotency-Key` header.
- **Pagination:** Every list endpoint is paginated (`cursor` pagination preferred for live feeds).
- **Errors:** Consistent error envelope `{ error: { code, message, details } }`. Never leak stack traces to clients.
- **Logging:** Structured JSON logs. Never log PHI (names, phone, medical history) — log resource IDs only.
- **Tests:**
  - Backend: pytest, ≥ 80% coverage on the emergency workflow path specifically. Unit + integration tests for the notification fan-out, RBAC, and PDF generation.
  - Dashboard: Vitest + React Testing Library on critical components.
  - Flutter: widget tests for the emergency button and profile screens.
- **Linting:** ruff + black for Python, ESLint + Prettier for TS, `flutter analyze` for Dart. CI fails on lint errors.
- **Secrets:** Never commit. Use `.env.example` files. Document required env vars in `docs/setup.md`.

---

## 11. UI/UX Guardrails (Elderly Users)

This is the single most under-appreciated requirement.

- **Minimum tap target:** 56dp on mobile.
- **Minimum font size:** 18sp body, 22sp for primary actions.
- **Emergency button:** Red, full-width, takes at least 30% of the screen on the home tab, with both an icon AND text. Reachable from the home screen without scrolling.
- **High contrast** (WCAG AAA where feasible).
- **Confirmation for destructive actions** but **NO confirmation for the emergency button** — one tap fires the alert. The "are you sure?" appears AFTER as a 5-second "Cancel alert" countdown.
- **Voice-over labels** on every interactive element.
- **English now, but** wrap every user-facing string in `tr('...')` so Telugu and Hindi can be added in Phase 2.

---

## 12. What to Document as You Build

In `/docs`, maintain these files and update them with each PR:

- `architecture.md` — System diagram, component responsibilities, data flow for the emergency path.
- `api-contracts.md` — OpenAPI spec link + notes on auth and idempotency.
- `data-model.md` — ER diagram + table descriptions.
- `emergency-sop.md` — The exact workflow from §7, plus failure modes and how the system handles them.
- `compliance-notes.md` — DPDP Act mapping, Telemedicine fields captured, CDSCO boundary statement, and any open legal questions flagged for the user's legal advisor (you are not a lawyer; flag, don't decide).
- `setup.md` — How to run locally, env vars, seed data.

---

## 13. What NOT To Do

- ❌ Do not invent compliance answers. If you're unsure whether something complies with DPDP / ABDM / CDSCO, **add it to `compliance-notes.md` as an open question for the user's legal advisor.** Do not silently make a legal call.
- ❌ Do not add AI / ML features. Not now.
- ❌ Do not build wearable integration. Not now.
- ❌ Do not let the builder admin role see any PHI. Ever. Test this explicitly.
- ❌ Do not use "diagnosis" language anywhere in UI or API responses.
- ❌ Do not couple the SMS/voice provider tightly to business logic. Hide it behind a `NotificationGateway` interface so it's swappable.
- ❌ Do not skip the audit log on any patient-data access path.
- ❌ Do not deploy to a public URL without TLS, rate limiting, and basic WAF rules.
- ❌ Do not commit secrets. Not even in a comment. Not even in a test fixture.

---

## 14. Definition of Done for MVP

The MVP is done when **all** of the following are true:

1. A resident on a real Android device can install the app, register, fill their profile, upload a record, and tap the emergency button.
2. Within 10 seconds of the tap, the on-duty doctor receives: a push notification AND an SMS AND a voice call.
3. The doctor can open the dashboard on a laptop, see the alert, see the full patient profile, mark acknowledged, mark on-site, record vitals, and either close the case or generate a Hospital Handover PDF.
4. The PDF, when generated, contains every field listed in §8 and is accessible via a signed S3 link valid for 15 minutes.
5. The builder admin role, when logged in, sees the admin dashboard with KPIs but **cannot** click into any patient record. This is tested with an automated RBAC test.
6. The full emergency path has automated tests that pass in CI.
7. The app shows a visible "Call 108" button on the splash screen even when not logged in.
8. All compliance open questions are listed in `compliance-notes.md` with a clear `[NEEDS_LEGAL_REVIEW]` tag.
9. `docs/setup.md` lets a new developer get the system running locally in under 30 minutes.

---

## 15. Final Reminders

- This is healthcare. Slow and correct beats fast and broken.
- When in doubt, ask. Pattern: ask one focused question, wait for an answer, then proceed.
- If you find ambiguity in this brief, **flag it** instead of guessing.
- Every PR description must include: what it does, what it doesn't do, what was tested, and any compliance implications.
- The emergency path is the only feature where reliability matters more than elegance. Over-engineer it. Add retries, fallbacks, and logs. Then add more.

---

**Now, go back to Section 0 and start there.**
