# Slice 7 Handoff (temporary — delete after Slice 7 lands)

> Purpose: everything needed to start Slice 7 (case lifecycle) without relying
> on chat-only context. Scope per `PLAN.md` Slice 7.

## Branch base

- Slice 6 lives **only** on branch `slice-6-emergency-hardening`.
- Base commit: **`906d77e`** (`fix: Slice 6 review #2 — concurrency-safe
  outbox, scoped duty phone, post-confirm continuity`), which includes this
  handoff doc as its tip.
- It is **not** merged to `master`/`main` (awaiting greenlight). Branch Slice 7
  from the current `slice-6-emergency-hardening` HEAD — branching from an
  earlier point will be missing Slices 1–6.

## Slice 7 scope (PLAN.md)

Case lifecycle: `acknowledged → en_route → on_site → treated_on_site /
escalated → closed`; vitals; notes (Telemedicine fields: doctor name,
medical-council registration number, consultation timestamp, advice given,
patient consent flag); `audit_log` + `case_events` row on every transition;
KPIs compute.

## Integration points from Slice 6 (must be honored)

1. **`acknowledged_at` is load-bearing.** `emergency_service.escalate_stale_alerts`
   (60s no-ack backup escalation) and the resident-owned endpoint
   `GET /api/v1/emergency/alerts/{case_id}/status` both key off
   `case.acknowledged_at is None` **and** `status == 'alerted'`. When Slice 7
   implements *acknowledge*, it must set `acknowledged_at` and move `status`
   off `alerted` so escalation stops firing and the mobile 60s fallback
   countdown resolves to "acknowledged".

2. **`case_events.event_type` namespace.** Already in use:
   `alert_created`, `backup_escalated`, `fallback_invoked`. Lifecycle
   transitions must add new `event_type` values and not collide. In
   particular `backup_escalated` doubles as the escalation idempotency
   marker — do not repurpose or emit it from lifecycle code.

3. **Escalation row locking.** `escalate_stale_alerts` selects candidate
   cases `FOR UPDATE`. Lifecycle writes to the same `emergency_cases` rows
   must commit promptly and not hold a long transaction over a case row, to
   stay consistent with that lock (Postgres enforces; SQLite no-ops).

4. **`NotificationStatus.SENDING`** is a new transient/claimed value (Slice 6
   review #2 outbox claim). Any new lifecycle UI or queries over
   `notification_attempts` must treat `sending` as in-flight, not terminal
   (terminal = `sent` / `failed` / `delivered` / `acknowledged`).

5. **Enums already cover the lifecycle — keep both in sync.**
   `CaseStatus` in `apps/backend/app/enums.py` and
   `packages/shared-types/src/enums.ts` already define
   `acknowledged, en_route, on_site, treated_on_site, escalated, closed`.
   There is no automated enum drift check until a later slice, so update both
   files together if any value is added.

## Verification cadence (unchanged)

Implement → verify → commit → stop for review; do not push to `master`/`main`.
Per-slice verification: backend `ruff` + `pytest` + `alembic upgrade head` +
`alembic check`; `shared-types` typecheck; dashboard typecheck/lint/build;
Flutter `analyze` + `test`.
