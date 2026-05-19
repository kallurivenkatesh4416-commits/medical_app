# Open Questions — Operational

> Operational / business unknowns hit during implementation. Tagged
> `[NEEDS_OPS_DECISION]`. Implementation continues with a sensible **technical
> default** (recorded here) and never invents business policy. Legal questions go to
> `docs/compliance-notes.md` under `[NEEDS_LEGAL_REVIEW]` instead.
>
> The "Builder Pilot Operating Plan" (doctor staffing, night/weekend coverage, SLA
> promises, ambulance contracts, pricing, resident onboarding ops, patient-worsens
> liability) is the user's **separate business document** — not in this repo, not
> stubbed, not committed.

## Items

### `[NEEDS_OPS_DECISION]` Backup-escalation trigger infrastructure
- **Question:** What runs the 60s no-ack backup escalation in production — a
  cron/worker, a queue with delayed messages, or an external scheduler?
- **Why it matters:** Escalation must fire reliably even with no inbound
  request; the timing is a patient-safety SLA, not a best-effort job.
- **Technical default applied:** the escalation logic lives in a single
  idempotent service function (`emergency_service.escalate_stale_alerts`)
  exposed via `POST /api/v1/emergency/escalations/run` (ops-only,
  tenant-scoped). A scheduler/worker calls the same function unchanged once
  infra lands; the `backup_escalated` `case_events` row makes repeat runs safe.
- **Surfaced in:** Slice 6 / `app/services/emergency_service.py`,
  `app/api/emergency.py`

### `[NEEDS_OPS_DECISION]` Mobile pending-alert store location
- **Question:** Where should the durable pending-alert file live in
  production — an app-private directory (via `path_provider`) rather than the
  OS temp dir?
- **Why it matters:** The Slice 6 review made the offline alert durable
  (`FilePendingAlertStore`, idempotency key persisted until the server
  confirms, resumed on launch). The default path is `Directory.systemTemp` to
  avoid a `path_provider` dependency now; the OS may evict temp files.
- **Technical default applied:** file-backed store under systemTemp, injected
  into the controller; production swaps in an app-private dir once the mobile
  storage dependency lands. Behaviour and tests are unchanged by the path.
- **Surfaced in:** Slice 6 review / `apps/mobile/lib/emergency_api.dart`

### `[NEEDS_OPS_DECISION]` Mobile fallback-tap offline durability
- **Question:** When a fallback button is tapped with no connectivity, the
  `case_events` record write is best-effort and currently dropped on failure.
  Is a persisted local outbox + later sync required for the audit/KPI trail?
- **Why it matters:** The dial always proceeds (patient safety first), but the
  "which fallback was used" signal can be lost offline.
- **Technical default applied:** dial first, record best-effort; a failed
  record does not block or delay the call. Local outbox deferred to the
  Slice 12 hardening pass.
- **Surfaced in:** Slice 6 / `apps/mobile/lib/emergency.dart`

### `[NEEDS_OPS_DECISION]` <short title>
- **Question:** …
- **Why it matters:** …
- **Technical default applied:** … (so build continues)
- **Surfaced in:** Slice N / file

## Technical hardening backlog (non-blocking, `[HARDENING]`)

### `[HARDENING]` Stuck-claim reaper for notification outbox
- **What:** delivery atomically claims an attempt (`queued → sending`) before
  calling the provider so concurrent delivery/replay cannot double-send. If a
  process dies after claiming but before writing `sent`/`failed`, the row is
  stuck in `sending` and is never retried (only `queued` rows resume).
- **Why deliberate:** in an emergency, dropping one duplicate page is safer
  than double-paging; the crash window is small.
- **Plan:** a reaper that re-queues `sending` rows older than N seconds (with
  an attempt counter / cap), plus the Postgres-backed concurrency test below.
  Fits the Slice 12 hardening pass.
- **Surfaced in:** Slice 6 review #2 / `emergency_service._claim_attempt`.

### `[HARDENING]` Postgres-backed concurrency test for refresh rotation
- **What:** `rotate_refresh` relies on `SELECT ... FOR UPDATE` row locking. The
  lock path only exists on Postgres; local/CI tests run on SQLite (which
  ignores `FOR UPDATE`), so the true multi-process race is not exercised.
- **Plan:** add a Postgres service to CI and a concurrency test that fires two
  simultaneous refreshes and asserts exactly one survives and replay revokes
  the family. Fits the Slice 12 hardening pass.
- **Surfaced in:** Slice 2 review (auth_service.rotate_refresh).
