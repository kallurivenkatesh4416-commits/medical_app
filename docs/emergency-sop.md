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
- Still Slice 6: SMS, voice, active `on_call_schedules`, backup escalation,
  mobile offline queue/retry, and the 60s fallback action sheet.
