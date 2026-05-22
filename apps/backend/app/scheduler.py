"""Slice 17 — periodic runner for the two emergency SLAs.

Resolves the ``[NEEDS_OPS_DECISION] Backup-escalation trigger
infrastructure`` open question from `docs/open-questions.md`. The 60s
no-ack backup escalation and the stuck-claim reaper were previously
only exposed as ops-triggered API endpoints — patient safety required a
reliable runner.

**No duplicate business logic.** The runner is a thin loop over the two
existing tenant-scoped service functions. Both are already idempotent
(Slice 6 / Slice 12 invariants); the runner just ensures they fire on
time across every project.

Modes:

- ``--mode=loop`` (default) — sleep / tick / sleep, suitable for a
  docker-compose sidecar or an ECS Fargate "scheduler" task.
- ``--mode=once`` — single pass and exit, suitable for AWS EventBridge
  → Lambda or a Kubernetes CronJob.

Idempotency story:

- ``escalate_stale_alerts`` selects candidate cases ``FOR UPDATE`` and
  refuses to insert a second ``case_events.backup_escalated`` row when
  one already exists. Two concurrent scheduler instances therefore
  serialize on the case row (Postgres only — SQLite ignores the lock,
  so prod is the load-bearing surface).
- ``requeue_stuck_notification_attempts`` is age-gated by
  ``NOTIFICATION_STUCK_CLAIM_SECONDS`` and only moves rows whose
  ``status='sending'`` AND ``attempted_at <= cutoff``. A fresh claim
  updates ``attempted_at`` so the reaper cannot recycle an active
  provider call.

Worst-case latency: backup paging fires within
``EMERGENCY_ACK_TIMEOUT_SECONDS + SCHEDULER_TICK_SECONDS`` of the alert
(default 60 + 5 = 65 s).

Production mapping (`docs/SLICE17-NOTES.md`):

- ECS Fargate: identical container, separate task with command override
  ``["python", "-m", "app.scheduler", "--mode", "loop"]``.
- Kubernetes: a `Deployment` (or `CronJob` for `--mode=once`) running
  the same image.
- AWS EventBridge: a rule firing every N seconds → Lambda calling
  ``run_once`` (or a Lambda calling the existing
  ``POST /api/v1/emergency/escalations/run`` endpoint per project).
"""

from __future__ import annotations

import argparse
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine
from app.logging import configure_logging, get_logger
from app.models.project import Project
from app.services.emergency_service import (
    escalate_stale_alerts,
    requeue_stuck_notification_attempts,
)

_log = get_logger("scheduler")


@dataclass
class TickResult:
    """Per-tick counters — useful for tests and emit on every non-idle
    pass so ops can see throughput in the logs without polling a metric."""

    escalations: int = 0
    reaped: int = 0
    projects_scanned: int = 0
    errors: int = 0

    @property
    def idle(self) -> bool:
        return self.escalations == 0 and self.reaped == 0


SessionFactory = Callable[[], Session]


def _default_session_factory() -> Session:
    """Default factory uses the module-level engine. Tests can pass any
    callable returning a fresh ``Session`` bound to the same engine.
    `Session` is its own context manager (``with factory() as session``),
    so this stays a one-liner."""
    return Session(engine)


def _iter_project_ids(session: Session) -> list[Any]:
    """Returns every project id. The runner is intentionally tenant-
    parallel (each project's queries are independent), so a single pass
    handles all projects. If the project count grows past a few hundred
    we can shard by project hash across multiple runner instances."""
    return [p.id for p in session.exec(select(Project)).all()]


def run_once(
    *,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> TickResult:
    """One pass — both jobs across every project.

    Each project is processed in its own short transaction so a single
    project's lock contention or error cannot freeze the rest of the
    fleet. The two jobs share a session within a project because each
    `service.commit()` already finalises its own write; using a single
    session keeps the connection-pool footprint small.

    Errors in one project are logged and counted — never re-raised — so
    the runner stays alive. (A pathological project failing every tick
    will spam logs but cannot starve other projects.)
    """
    factory = session_factory or _default_session_factory
    result = TickResult()
    with factory() as session:
        project_ids = _iter_project_ids(session)
    result.projects_scanned = len(project_ids)
    for pid in project_ids:
        try:
            with factory() as session:
                escalated = escalate_stale_alerts(
                    session,
                    project_id=pid,
                    actor_user_id=None,
                    from_ip=None,
                    now=now,
                )
                result.escalations += len(escalated)
            with factory() as session:
                reaped = requeue_stuck_notification_attempts(
                    session,
                    project_id=pid,
                    actor_user_id=None,
                    from_ip=None,
                    now=now,
                )
                result.reaped += len(reaped)
        except Exception as exc:  # noqa: BLE001
            result.errors += 1
            _log.error(
                "scheduler_project_error",
                project_id=str(pid),
                error=exc.__class__.__name__,
            )
    return result


def run_loop(
    *,
    tick_seconds: int | None = None,
    stop_event: threading.Event | None = None,
    max_iterations: int | None = None,
    session_factory: SessionFactory | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Loop body. Exits cleanly on:

    - SIGTERM / SIGINT (production shutdown),
    - ``stop_event.set()`` (tests / cooperative shutdown),
    - ``max_iterations`` reached (tests).

    Returns the number of completed ticks so tests can assert exact
    iteration counts. ``sleep`` and ``session_factory`` are injectable
    seams so tests run with no real time passing.
    """
    settings = get_settings()
    tick = tick_seconds if tick_seconds is not None else settings.scheduler_tick_seconds
    if tick < 1:
        tick = 1  # defensive — config of 0 would peg a CPU core
    own_event = stop_event is None
    stop_event = stop_event or threading.Event()
    if own_event:
        # Only register signal handlers when we own the event — tests
        # supply their own and would not want our handlers globally
        # installed inside the test process.
        def _on_signal(_sig: int, _frame: object) -> None:
            stop_event.set()

        try:
            signal.signal(signal.SIGTERM, _on_signal)
            signal.signal(signal.SIGINT, _on_signal)
        except ValueError:
            # `signal.signal` works only in the main thread. If the
            # caller runs the loop from a worker thread, fall back to
            # the stop-event path (cooperative shutdown only).
            pass

    completed = 0
    while not stop_event.is_set():
        try:
            result = run_once(session_factory=session_factory)
            if not result.idle or result.errors:
                _log.info(
                    "scheduler_tick",
                    escalations=result.escalations,
                    reaped=result.reaped,
                    projects=result.projects_scanned,
                    errors=result.errors,
                )
        except Exception as exc:  # noqa: BLE001
            # `run_once` already catches per-project errors; this catch
            # protects against pathological infrastructure failures
            # (database unreachable, etc.) so the runner stays alive.
            _log.error("scheduler_tick_error", error=exc.__class__.__name__)
        completed += 1
        if max_iterations is not None and completed >= max_iterations:
            break
        # Use wait() so SIGTERM / stop_event.set() preempt the sleep —
        # `time.sleep` would block for the full tick before checking.
        stop_event.wait(timeout=tick)
    return completed


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="med-emergency-scheduler",
        description=(
            "Periodic runner for backup escalation + stuck-claim "
            "notification reaper (Slice 17)."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["loop", "once"],
        default="loop",
        help=(
            "loop = run forever with periodic ticks; "
            "once = single pass and exit (for cron / Lambda)."
        ),
    )
    parser.add_argument(
        "--tick-seconds",
        type=int,
        default=None,
        help="Override SCHEDULER_TICK_SECONDS from the environment.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    _log.info(
        "scheduler_starting",
        env=settings.app_env,
        provider_mode=settings.provider_mode,
        mode=args.mode,
        tick_seconds=args.tick_seconds or settings.scheduler_tick_seconds,
    )

    if args.mode == "once":
        result = run_once()
        _log.info(
            "scheduler_once_complete",
            escalations=result.escalations,
            reaped=result.reaped,
            projects=result.projects_scanned,
            errors=result.errors,
        )
        return

    completed = run_loop(tick_seconds=args.tick_seconds)
    _log.info("scheduler_stopped", ticks=completed)


if __name__ == "__main__":  # pragma: no cover - exercised in container
    main()
