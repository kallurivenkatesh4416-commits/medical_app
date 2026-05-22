# Slice 17 Notes — Scheduler infrastructure

> Purpose: capture the Slice 17 surface area, the test cadence, and
> the integration points the next slice should honour. Same shape as
> `SLICE11-HANDOFF.md` / `SLICE14-NOTES.md` / `SLICE16-NOTES.md`.

## Branch state

- Slice 17 lands on the working branch `slice-6-emergency-hardening`
  (still the working base — Slice 14 / 16 / 16-review-#1 already on
  it). `main` untouched; merge plan in `docs/SLICE13-MERGE-PLAN.md`.

## What this slice delivers

The second P0 from the most recent product audit:

> "P0: The 60-second backup escalation SLA is still manual."

Both `escalate_stale_alerts` and `requeue_stuck_notification_attempts`
are now driven by a periodic runner process — no human or external
runner has to POST the ops endpoints to keep the patient-safety SLAs
ticking. The ops endpoints stay live so the on-call team can still
trigger an emergency reaper pass during incident response.

### Backend

1. **`apps/backend/app/scheduler.py`** — runner module.
   - `run_once(*, session_factory=None, now=None) -> TickResult` —
     one pass across every project. Each project gets its own short
     transaction so a per-project DB error / lock cannot freeze the
     fleet. Errors are counted, not re-raised.
   - `run_loop(*, tick_seconds, stop_event, max_iterations, session_factory, sleep)` —
     loop body. Exits cleanly on SIGTERM / SIGINT, `stop_event.set()`,
     or `max_iterations`. Uses `stop_event.wait(timeout=tick)` so a
     shutdown signal preempts the tick sleep instead of blocking for
     the full window. Signal handlers register only on the main
     thread (cooperative shutdown otherwise — tests rely on this).
   - `main()` — argparse entry point with `--mode={loop,once}` and
     `--tick-seconds=N` override. Default mode is `loop`.
2. **Config**: new `SCHEDULER_TICK_SECONDS` (default 5) in
   `app/config.py` and `.env.example`. Worst-case backup paging time
   is `EMERGENCY_ACK_TIMEOUT_SECONDS + SCHEDULER_TICK_SECONDS`
   (default 60 + 5 = 65 s).
3. **`docker-compose.yml`**: new `scheduler` service.
   - Same backend image; command overridden to
     `["python", "-m", "app.scheduler", "--mode", "loop"]`.
   - `depends_on: postgres (service_healthy)`; the backend service
     handles `alembic upgrade head` so the scheduler is safe to start
     in parallel.
   - `restart: unless-stopped` — the runner stays up across
     transient failures, matching the production reliability story.

### Tests

`tests/test_scheduler.py` — **9 new tests**, none flaky (loops use
the injected `sleep` so no real time passes).

| Test | What it proves |
|---|---|
| `test_run_once_escalates_stale_alerts_across_every_project` | multi-tenant fan-out: two projects each get their stale alert paged |
| `test_run_once_is_idempotent_across_ticks` | the natural tick cadence (seconds) does not double-escalate — one `backup_escalated` row per case across two passes |
| `test_run_once_requeues_stuck_attempts_across_every_project` | the reaper runs per project on each tick |
| `test_run_once_is_quiet_with_no_work_to_do` | idle ticks write no rows (audit / log churn invariant) |
| `test_run_once_isolates_per_project_errors` | a pathological project (simulated transient DB error) does not halt processing of the other projects; the error is counted |
| `test_run_loop_honors_max_iterations` | loop exits after N ticks (test-friendly seam) |
| `test_run_loop_honors_stop_event` | cooperative shutdown — pre-set event causes zero iterations and immediate exit |
| `test_run_loop_calls_runner_once_per_iteration` | one `run_once` call per tick — no batching, no skipping |
| `test_run_loop_survives_run_once_exception` | catastrophic `run_once` failure (DB unreachable, etc.) is swallowed so the loop stays alive for the next tick |

Concurrency note: the underlying `SELECT ... FOR UPDATE` lock in
`escalate_stale_alerts` (proven in production-only Postgres; SQLite
ignores the lock) is the real race boundary against two concurrent
scheduler instances. SQLite tests cover the sequential idempotency
case — multi-instance Postgres contention is the same row-lock
pattern as refresh rotation, still tracked under `[HARDENING]
Postgres-backed concurrency test for refresh rotation` in
`docs/open-questions.md`.

## What this slice deliberately does NOT do

Out of scope per the slice ask:

- **No auth perimeter hardening.** JWT_SECRET validation, OTP rate
  limits, login lockout — all tracked as P1 in the most recent
  product audit; targeted by the upcoming hardening pass.
- **No upload MIME sniffing / virus scanning.** Slice 4 records
  trust the caller-declared content_type today; that's P1 work.
- **No dashboard rebuild.** Slice 15 (next per your ordering).
- **No Postgres-CI / load proof.** `[HARDENING] Postgres-backed
  concurrency test for refresh rotation` stays in open-questions.md;
  this slice could not justify standing up Postgres in CI just for
  the scheduler — the runner's correctness is proven at the
  service-function level (Slice 6/12 invariants) and at the
  multi-tenant level (these new tests).
- **No new ops endpoints / no removal of existing ones.** The
  `POST /api/v1/emergency/escalations/run` and
  `POST /api/v1/emergency/notifications/requeue-stuck` endpoints
  stay live — the on-call team needs them for incident response
  (e.g. "run the reaper now after that DB hiccup we just had").

## Integration points the next slice should honour

### 1. The runner is the only thing driving the SLAs

After Slice 17 lands in production, the SLA timing is owned by the
scheduler process. If the scheduler container is killed and not
restarted:

- Backup escalations stop firing (no on-call doctor paged for stale
  alerts).
- Stuck `sending` notification rows accumulate.

Both are visible — `restart: unless-stopped` covers the local-dev
story. Production must ensure the equivalent (ECS service
desired-count = 1+, Kubernetes Deployment with restart policy, or a
keep-alive on the EventBridge cron). Healthcheck endpoint for the
scheduler is a future hardening item (today it's "process exists
and is not exiting").

### 2. Service-function signatures are the runner's contract

`scheduler.run_once` depends on:

- `escalate_stale_alerts(session, *, project_id, actor_user_id, from_ip, now)`
- `requeue_stuck_notification_attempts(session, *, project_id, actor_user_id, from_ip, now)`

A future slice that adds a third periodic job (e.g. medicine missed-dose
materialiser tracked in `compliance-notes.md`) should add it to
`run_once`'s per-project loop, not start a parallel scheduler.

### 3. Multi-scheduler safety relies on Postgres row locks

If a future deployment runs more than one scheduler instance (HA),
the duplicate-prevention story is:

- `escalate_stale_alerts` — `SELECT ... FOR UPDATE` on the case
  row serializes the check-then-insert of the `backup_escalated`
  marker.
- `requeue_stuck_notification_attempts` — atomic
  `UPDATE WHERE status='sending' AND attempted_at <= cutoff`; only
  the winning UPDATE moves the row to `queued`.

SQLite (test) ignores `FOR UPDATE`. The Postgres-contention test
remains a `[HARDENING]` item — leaving it documented rather than
silently extending CI keeps the open-questions list honest.

### 4. Single-process is the default

Slice 17 assumes one scheduler instance per environment. The
`max_iterations` + injected `sleep` are test-only seams; production
runs the loop unbounded. If a future slice needs N-of-N HA, the
extra work is (a) confirming Postgres contention behaviour, (b)
adding a healthcheck endpoint, (c) potentially adding a distributed
lock (e.g. Postgres advisory lock) so only one instance is
"primary" at a time. None of that is needed today.

### 5. Brief §14 DoD #2 — ten-second SLA

The brief specifies "Within 10 seconds of the tap, the on-duty
doctor receives push AND SMS AND voice." That's the FAN-OUT time —
covered by the synchronous send path in `_deliver_one` (Slice
6/12). Slice 17 covers the BACKUP-paging SLA (60 s no-ack →
backup doctor), which is a separate, complementary path.

## Verification baseline (carry forward)

| Suite | Result |
|---|---|
| Backend `ruff` | All checks passed |
| Backend `pytest` | **186 passed** (177 → +9 scheduler tests) |
| `shared-types` typecheck | Unchanged (no shared-types changes this slice) |
| Dashboard typecheck / lint / build | Unchanged (no dashboard work this slice) |
| `flutter analyze` | Unchanged |
| `flutter test` | Unchanged (73 passed — no mobile work this slice) |

## Production mapping cheat sheet

| Target | Wiring |
|---|---|
| Local dev | `docker compose up` — `scheduler` service auto-starts and ticks every 5s. |
| ECS Fargate | New task definition reusing the backend image; container command `python -m app.scheduler --mode loop`. Desired-count 1. Same `DATABASE_URL` / `.env`. |
| Kubernetes | `Deployment` with `replicas: 1` (or N with the Postgres-contention story above). Same image, command override. |
| AWS EventBridge → Lambda | Rule fires every N seconds → Lambda invokes `python -m app.scheduler --mode once` (or directly calls `scheduler.run_once()` if packaging the backend as a Lambda layer). |
| Kubernetes CronJob | `schedule: "*/1 * * * *"` with `command: ["python", "-m", "app.scheduler", "--mode", "once"]`. |

## Next-slice ordering (per the user's plan)

Slice 17 closes the second P0. Remaining audit items:

- **P1 #3** Auth perimeter (JWT secret validation, OTP rate limits) — hardening pass.
- **P1 #4** Dashboard rebuild — Slice 15 (next per the user's ordering: 16 → 17 → 15).
- **P1 #5** Upload MIME sniffing + virus scan — hardening pass.
- **P2 #6** Postgres-CI + load/p95 evidence — hardening pass.
- **P2 #7** CORS / proxy story for dashboard — falls out of Slice 15.

The user-stated next slice is **Slice 15 — dashboard rebuild on
shadcn/ui + Tailwind + TanStack Query**, real doctor login, component
decomposition, Vitest test coverage on the alert→PDF path.
