# API Contracts

> Living document — updated every slice.

- OpenAPI spec served by the backend at `/openapi.json` (Swagger UI at `/docs`).
- `packages/shared-types` is generated from this spec; CI checks for drift.

## Conventions

- **Auth:** OTP login → JWT short-lived access + refresh. RBAC enforced server-side.
- **Idempotency:** all resource-creating writes accept an `Idempotency-Key` header.
- **Pagination:** every list endpoint paginated; cursor pagination for live feeds.
- **Error envelope:** `{ "error": { "code", "message", "details" } }`. No stack
  traces to clients.

## Endpoints

| Method | Path | Slice | Notes |
|---|---|---|---|
| GET | `/healthz` | 1 | liveness |
| GET | `/readyz` | 1 | readiness (DB reachable) |
| POST | `/api/v1/auth/otp/request` | 2 | sends OTP (stub SMS in dev); `dev_otp` only when `APP_ENV=local` |
| POST | `/api/v1/auth/otp/verify` | 2 | verifies OTP → access + refresh tokens |
| POST | `/api/v1/auth/refresh` | 2 | rotates refresh (single-use, reuse-detected) |
| POST | `/api/v1/auth/logout` | 2 | revokes the refresh token (204) |
| GET | `/api/v1/auth/me` | 2 | current user (Bearer access token) |
| POST | `/api/v1/auth/otp/verify` | 3 | now returns either a token pair OR `registration_required` + `registration_token` |
| GET | `/api/v1/projects` | 3 | project lookup (names only; registration token) |
| POST | `/api/v1/onboarding/complete` | 3 | atomic resident self-registration → token pair; honours `Idempotency-Key` (retry → working session, not 409) |
| GET | `/api/v1/me/profile` | 3 | resident's own profile (PHI; audited) |
| GET | `/api/v1/me/consents` | 3 | resident's consents |
| PATCH | `/api/v1/me/consents/{consent_type}` | 3 | grant/revoke; `data_storage`=false closes account |
| GET | `/api/v1/residents/{id}/profile` | 3 | staff view (doctor/nurse/ops; PHI-role-blocked; consent-gated; audited) |
| POST | `/api/v1/me/records` | 4 | resident upload (multipart PDF/JPEG/PNG, tags, `Idempotency-Key`; audited) |
| GET | `/api/v1/me/records` | 4 | resident list of own medical records (audited) |
| GET | `/api/v1/me/records/{id}/link` | 4 | resident signed download link; TTL hard-capped at 900s |
| GET | `/api/v1/residents/{resident_id}/records` | 4 | staff list (doctor/nurse/ops; PHI-blocked, tenant-isolated, consent-gated, audited) |
| GET | `/api/v1/residents/{resident_id}/records/{id}/link` | 4 | staff signed download link; same guards as staff list |
| GET | `/api/v1/records/download/{token}` | 4 | local stub signed-link download; invalid/tampered/expired tokens return 401 |
| POST | `/api/v1/devices/push-token` | 5 | doctor/nurse/ops registers a push token for alert fan-out |
| POST | `/api/v1/emergency/alerts` | 5/6 | resident idempotent alert; records case + 3-channel fan-out (FCM+SMS+voice) to the on-call doctor, opt-in security-desk minimal payload |
| GET | `/api/v1/emergency/alerts/active` | 5 | doctor/nurse/ops active alert feed; PHI-blocked and tenant-scoped |
| GET | `/api/v1/emergency/alerts/{case_id}/status` | 6 | resident (owner) PHI-free `{status, acknowledged}`; drives the mobile fallback countdown (the staff feed is not resident-visible) |
| POST | `/api/v1/emergency/escalations/run` | 6 | ops-only, tenant-scoped; pages the backup doctor for alerts with no ack within the window. Idempotent; manual entry point until scheduler infra lands |
| POST | `/api/v1/emergency/notifications/requeue-stuck?older_than_seconds=N` | 12 | ops-only, tenant-scoped; re-queues and redelivers notification attempts stuck in `sending` after a provider-process crash; audited |
| GET | `/api/v1/emergency/alerts/{case_id}/fallback-numbers` | 6 | resident (owner) fallback numbers; 108/112 are constants, the rest resolved from project / on-call / contacts; audited |
| POST | `/api/v1/emergency/alerts/{case_id}/fallback` | 6/12 | resident (owner) records a fallback-sheet tap (`channel`) to `case_events`; audited. Optional `Idempotency-Key` lets the mobile offline outbox replay without duplicating the event. Does not cancel the retrying alert |
| GET | `/api/v1/emergency/alerts/{case_id}` | 7 | doctor/nurse/ops case detail with vitals, notes, and timeline; PHI-blocked, tenant-scoped, audited |
| POST | `/api/v1/emergency/alerts/{case_id}/transition` | 7 | doctor/nurse lifecycle transition (`acknowledged → en_route → on_site → treated_on_site/escalated → closed`); every transition writes `case_events` + audit |
| POST | `/api/v1/emergency/alerts/{case_id}/vitals` | 7 | doctor/nurse records manual vitals after `on_site`; audited and timeline-backed |
| POST | `/api/v1/emergency/alerts/{case_id}/notes` | 7 | doctor/nurse records case notes; treatment/escalation notes require doctor role; treatment notes capture Telemedicine fields |
| GET | `/api/v1/emergency/kpis` | 7 | PHI-free project aggregate KPIs (`total/active/closed`, average ack/on-site seconds); builder_admin can read |
| POST | `/api/v1/emergency/alerts/{case_id}/handover` | 8 | doctor-only; generates a §8-complete PDF, stores it encrypted, returns the signed link (TTL hard-capped at 900s) and freezes the doctor's name + registration number into the row |
| GET | `/api/v1/handover/{handover_id}/link` | 8 | doctor/nurse/ops; mints a fresh ≤15-min signed link without re-rendering; audited |
| POST | `/api/v1/handover/{handover_id}/dispatch` | 8 | doctor-only; sends the signed link by email (Mailhog dev / SMTP live) and/or WhatsApp (Twilio); one channel failing must not block the other; persists one `handover_dispatches` row per channel |
| GET | `/api/v1/handover/file/{token}` | 8 | capability-token-gated PDF download (token type distinct from `record_url`); 401 on invalid/expired; 404 on missing; audited |
| POST | `/api/v1/me/medicines/schedules` | 9 | resident creates own medicine schedule; gated by `MEDICINE_REMINDER_NOTIFICATIONS` consent (live state); `times_of_day` count must match `frequency`; audited |
| GET | `/api/v1/me/medicines/schedules` | 9 | resident lists own (active + recently stopped) schedules; audited |
| POST | `/api/v1/me/medicines/schedules/{id}/deactivate` | 9 | resident stops own schedule; mobile cancels local reminders on next sync |
| POST | `/api/v1/me/medicines/doses` | 9 | resident logs `taken` / `skipped` for a slot; unique on `(schedule_id, scheduled_for)` so a network-retry returns the existing row, not 409 |
| POST | `/api/v1/residents/{resident_id}/medicines/schedules` | 9 | doctor-only; creates schedule on behalf of resident; same consent gate + tenant isolation |
| GET | `/api/v1/residents/{resident_id}/medicines/schedules` | 9 | doctor / nurse / ops list; PHI-blocked for builder/security; `EMERGENCY_SHARE_WITH_DOCTOR` consent-gated; audited |
| POST | `/api/v1/residents/{resident_id}/medicines/schedules/{id}/deactivate` | 9 | doctor stops a schedule |
| GET | `/api/v1/residents/{resident_id}/medicines/adherence?days=N` | 9 | doctor / nurse / ops aggregate adherence (`taken`/`skipped`/`missed`/`scheduled_slots`); `missed` computed live from schedule + logs (no scheduler yet); 1–90 day window |
| GET | `/api/v1/admin/kpis?days=N` | 10 | doctor / nurse / ops / builder_admin project aggregate KPIs only: emergency counts/response times, residents onboarded, records uploaded, medicine adherence totals; audited; no PHI identifiers |
| GET | `/api/v1/admin/exports/monthly?month=YYYY-MM&format=csv\|pdf` | 10 | direct monthly aggregate export (CSV or PDF); no StorageGateway/signed link because no PHI; audited with `ADMIN_EXPORT_GENERATED` |

Auth: `Authorization: Bearer <access>`. Access tokens are short-lived JWTs;
refresh tokens are opaque, stored hashed, rotated on every use.

_Endpoints added per slice._
