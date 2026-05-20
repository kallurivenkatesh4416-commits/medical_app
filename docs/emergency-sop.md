# Emergency SOP

> Living document — the exact workflow + failure modes. Built out in Slices 5–7.
> Authoritative scope: `../PROJECT_BRIEF.md` §7 and `../PLAN.md`.

## Workflow (target)

1. Resident taps "I Need Medical Help" → app captures resident, optional symptom,
   GPS (if granted), timestamp → idempotent `POST /api/v1/emergency/alerts`.
2. Backend creates `emergency_case` (status `alerted`) → parallel fan-out:
   FCM push + SMS + voice to the active on-call doctor (+ nurse/ops/family;
   `security_desk` minimal payload only if project opted in). Each channel logs to
   `notification_attempts`.
3. No acknowledgment in 60s → auto-escalate to backup (from `on_call_schedules`)
   **and** the mobile failed-alert fallback sheet appears (Call doctor / 108 / 112 /
   family / security desk). Original alert keeps retrying in the background.
4. Doctor acknowledges → on-site → vitals → notes → outcome (treated / escalate /
   observe) → closure. Every transition writes `audit_log` + `case_events`.

## Failure modes (to detail per slice)

- Push fails → SMS + voice still fire (independent logging).
- Device offline → local queue, retry every 5s, visible "Alert sent / Retrying".
- Backend unreachable past 60s → fallback action sheet with `tel:` dialer.

## Slice 5 status

- Implemented happy path: resident creates an idempotent alert, backend records
  `emergency_case(status=alerted)`, writes `case_events.alert_created`, attempts
  one FCM push to the primary active doctor, logs `notification_attempts`, and
  exposes the doctor/nurse/ops active-alert feed.
- The dashboard polls `/api/v1/emergency/alerts/active` and shows the live red
  alert banner once a doctor token is supplied.
## Slice 6 status

- **3-channel fan-out:** alert creation now pages the on-call doctor over FCM
  push **+** SMS **+** voice. Each channel logs to `notification_attempts`
  independently; one channel failing (no push token, provider exception) never
  blocks the others or the case. Commit-before-send ordering preserved.
- **On-call resolution:** primary doctor / backup doctor / security desk are
  resolved from `on_call_schedules` (active window, `is_backup` flag).
  Primary doctor falls back to "first active doctor" only when no schedule
  exists (documented, so an alert is never doctor-less). Backup has no fallback.
- **60s backup escalation:** `escalate_stale_alerts` pages the active backup
  for ALERTED cases with no `acknowledged_at` past
  `EMERGENCY_ACK_TIMEOUT_SECONDS`. Idempotent via the `backup_escalated`
  `case_events` marker; writes `EMERGENCY_ALERT_ESCALATED` audit. Triggered by
  `POST /emergency/escalations/run` (ops-only) until scheduler infra lands.
- **Security desk (opt-in, minimum-necessary):** only when
  `project.enable_security_desk_alerts` **and** a security-desk on-call exists.
  Payload is name + flat/villa + location + primary contact phone + case id —
  never symptoms/vitals/history/notes/records.
- **Mobile:** one-tap "I Need Medical Help" → send → offline queue retrying
  every 5s in the background → a 60s countdown that, on no server ack, opens a
  large high-contrast fallback sheet (Call doctor / 108 / 112 / primary family
  / security desk — last two only when resolved). Every tap dials and records
  a `case_events` row via `POST /emergency/alerts/{id}/fallback`; the original
  alert keeps retrying (the countdown never cancels it). All numbers except
  108/112 come from `GET /emergency/alerts/{id}/fallback-numbers`.
- **Live providers:** the fan-out is provider-agnostic and fully covered
  against the stub (including "kill push, others still deliver"). The concrete
  live Twilio/FCM gateway is wired on `PROVIDER_MODE=live` once the operator
  supplies keys (PLAN.md Slice 6 "Needs your keys") — verified end-to-end with
  real keys, not in CI.
### Slice 6 review fixes (reliability contract)

- **Durable outbox:** the planned notification attempts are written as
  `queued` rows **in the same transaction as the case/event/audit/idempotency
  rows**. Delivery happens after commit; a crash in between leaves a persisted
  case *with* its queued attempts, and the idempotent retry re-runs
  `_deliver_pending` to finish them. Delivery is idempotent (only `queued`
  rows are sent), so create + replay never double-send.
- **Resident-visible ack:** `GET /emergency/alerts/{id}/status` is a
  resident-owned, PHI-free read so the mobile 60s countdown checks the real
  acknowledgment instead of the staff-only `/active` feed (which would 403 a
  resident and falsely show the fallback sheet).
- **Retry-safe idempotency:** the mobile idempotency key is generated once per
  tap, reused on every retry, and persisted (file-backed `PendingAlertStore`)
  until the server confirms a case id — so a lost response or an app kill
  while offline cannot create duplicate cases; `restore()` resumes with the
  same key on next launch.
- **On-call integrity:** resolution now verifies the scheduled user is still
  active **and** in the scheduled project **with** the scheduled role; bad
  schedule data can no longer page the wrong tenant/role.
- **Escalation race:** the no-ack candidate cases are selected `FOR UPDATE`,
  so two concurrent escalation runs serialize and the `backup_escalated`
  marker cannot be double-inserted (SQLite ignores the lock; Postgres
  enforces it).

### Slice 6 review #2 fixes (concurrency + continuity)

- **Concurrency-safe outbox:** delivery now atomically claims each attempt
  (`queued → sending` via a conditional UPDATE) before calling the provider.
  A concurrent original-delivery and lost-response replay can no longer both
  send the same attempt — the loser of the claim simply skips. A claimed-but-
  unfinished row (provider crash) stays `sending` until the Slice 12 stuck-
  notification reaper age-gates, requeues, audits, and redelivers it.
- **Duty-phone scoping:** `_contact_phone_for` now filters the on-call lookup
  by the case's `project_id` **and** the recipient's role, so overlapping/
  stale schedule rows in another project or role cannot supply the wrong
  number.
- **Post-confirmation continuity:** the mobile pending record now persists the
  server `caseId` once created. If the app is killed after creation but before
  acknowledgment, `restore()` resumes the **confirmed** path — it does *not*
  re-send (the case exists) but restarts the 60s countdown so the fallback
  sheet / status polling still fire. The record is cleared on acknowledgment
  or the first fallback tap.

- Still Slice 7: case lifecycle (`acknowledged`→…→`closed`), vitals, notes.

## Slice 7 status

- **Lifecycle:** staff transition cases through
  `acknowledged → en_route → on_site → treated_on_site/escalated → closed`.
  `acknowledged_at` is set on acknowledge, so Slice 6 backup escalation stops
  and the resident status endpoint returns `acknowledged=true`. `on_site_at`,
  `en_route_at`, `escalated_at`, and `closed_at` are recorded as applicable.
- **Timeline + audit:** every transition writes a distinct `case_events` row
  (`case_acknowledged`, `case_en_route`, `case_on_site`,
  `case_treated_on_site`, `case_escalated`, `case_closed`) and an
  `EMERGENCY_CASE_TRANSITIONED` audit row. Existing Slice 6 event names
  (`alert_created`, `backup_escalated`, `fallback_invoked`) remain untouched.
- **Vitals:** doctor/nurse can record manual BP, SpO2, HR, RR, and temperature
  after the case is marked on-site. Each write is audited and appears in the
  case detail timeline.
- **Notes:** doctor/nurse can add observation notes; treatment/escalation notes
  are doctor-only. Treatment notes require the Telemedicine fields captured by
  the schema: doctor name, registration number, consultation timestamp, advice,
  and patient consent flag.
- **KPIs:** `/emergency/kpis` returns PHI-free project aggregates (case counts,
  average alert→ack and alert→on-site seconds). `builder_admin` can read this
  endpoint, while patient-level case detail remains blocked.

- Still Slice 8: hospital handover PDF generation and dispatch.

## Slice 12 hardening status

- **Fallback tap durability:** mobile fallback taps now have a local outbox.
  If the resident taps Call doctor / 108 / 112 / family / security while the
  backend write is offline, the dialer still opens immediately and the app
  retries the tap later with the same idempotency key. The outbox stores only
  case id, channel, and idempotency key.
- **Idempotent fallback endpoint:** `POST /emergency/alerts/{id}/fallback`
  accepts an optional `Idempotency-Key`. A replay writes no duplicate
  `fallback_invoked` event or audit row; a same-key different-channel replay
  is rejected with `idempotency_key_conflict`.
- **Stuck notification recovery:** `POST /emergency/notifications/requeue-stuck`
  lets ops requeue/redeliver old `notification_attempts(status=sending)` rows
  after a provider-process crash. The reaper is tenant-scoped, age-gated by
  `NOTIFICATION_STUCK_CLAIM_SECONDS`, and audited with
  `EMERGENCY_NOTIFICATION_REQUEUED`.
- **Hardening proof:** `docs/slice12-hardening-report.md` records the emergency
  load smoke test, 2G latency simulation, offline fallback verification, and
  92.53% backend emergency API/service coverage gate.
