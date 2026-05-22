"""Slice 17 — scheduler runner tests.

The two service functions (`escalate_stale_alerts`,
`requeue_stuck_notification_attempts`) already have deep coverage in
`test_emergency.py`. These tests focus on the runner's own contract:

  - `run_once` iterates across every project (multi-tenant fan-out).
  - Repeated `run_once` passes do not double-escalate.
  - A pathological per-project error does not halt the loop or the
    other projects' processing.
  - `run_loop` honors `max_iterations`, `stop_event`, and uses the
    injectable `sleep` so tests run with no real time passing.

Concurrency: the underlying SELECT...FOR UPDATE lock in
`escalate_stale_alerts` is the real concurrency boundary (proven by
the Postgres path in production; SQLite ignores the lock — see the
`[NEEDS_OPS_DECISION]` note in `docs/open-questions.md`). These tests
exercise the runner's idempotency at the sequential-call level, which
mirrors the actual scheduler cadence (ticks are seconds apart).
"""

import threading
from datetime import date, timedelta
from typing import Any

import pytest
from sqlmodel import Session, select

from app import scheduler
from app.db import engine
from app.enums import (
    CaseStatus,
    NotificationChannel,
    NotificationStatus,
    Role,
)
from app.models.base import utcnow
from app.models.emergency import (
    CaseEvent,
    EmergencyCase,
    NotificationAttempt,
)
from app.models.on_call import OnCallSchedule
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User
from app.services.emergency_service import BACKUP_ESCALATED_EVENT

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _resident_with_user(
    session: Session, project: Project, make_user
) -> tuple[Resident, User]:
    user = make_user(Role.RESIDENT)
    user.project_id = project.id
    session.add(user)
    session.commit()
    session.refresh(user)
    resident = Resident(
        user_id=user.id,
        project_id=project.id,
        flat_villa_number="A-1",
        dob=date(1960, 1, 1),
        gender="prefer_not_to_say",
    )
    session.add(resident)
    session.commit()
    session.refresh(resident)
    return resident, user


def _stale_alert(
    session: Session,
    *,
    project: Project,
    resident: Resident,
    age_seconds: int = 120,
) -> EmergencyCase:
    """An ALERTED case with `alert_time` past the no-ack threshold."""
    case = EmergencyCase(
        project_id=project.id,
        resident_id=resident.id,
        created_by_user_id=resident.user_id,
        status=CaseStatus.ALERTED.value,
        alert_time=utcnow() - timedelta(seconds=age_seconds),
        symptom_codes=[],
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def _backup_doctor(session: Session, project: Project, make_user) -> User:
    doctor = make_user(Role.DOCTOR)
    doctor.project_id = project.id
    session.add(doctor)
    session.commit()
    session.refresh(doctor)
    now = utcnow()
    session.add(
        OnCallSchedule(
            project_id=project.id,
            role=Role.DOCTOR.value,
            user_id=doctor.id,
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(hours=1),
            is_backup=True,
        )
    )
    session.commit()
    return doctor


def _stuck_attempt(
    session: Session,
    *,
    project: Project,
    resident: Resident,
    recipient: User,
    age_seconds: int = 600,
) -> NotificationAttempt:
    """A claimed-but-never-finalized attempt the reaper should requeue."""
    case = EmergencyCase(
        project_id=project.id,
        resident_id=resident.id,
        created_by_user_id=resident.user_id,
        status=CaseStatus.ALERTED.value,
        symptom_codes=[],
    )
    session.add(case)
    session.flush()
    attempt = NotificationAttempt(
        project_id=project.id,
        case_id=case.id,
        channel=NotificationChannel.SMS.value,
        recipient_id=recipient.id,
        status=NotificationStatus.SENDING.value,
        provider_ref="SMstuck",
        attempted_at=utcnow() - timedelta(seconds=age_seconds),
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def _session_factory():
    """Tests share the in-memory engine; return a fresh Session per call."""
    return Session(engine)


# --------------------------------------------------------------------------- #
# run_once                                                                    #
# --------------------------------------------------------------------------- #


def test_run_once_escalates_stale_alerts_across_every_project(
    session: Session, make_user
):
    """Two projects, each with a stale alert; one tick must escalate
    both. `run_once` is the multi-tenant fan-out the runner depends on."""
    project_a = Project(name="Project A", enable_security_desk_alerts=False)
    project_b = Project(name="Project B", enable_security_desk_alerts=False)
    session.add(project_a)
    session.add(project_b)
    session.commit()
    session.refresh(project_a)
    session.refresh(project_b)

    resident_a, _ = _resident_with_user(session, project_a, make_user)
    resident_b, _ = _resident_with_user(session, project_b, make_user)
    _backup_doctor(session, project_a, make_user)
    _backup_doctor(session, project_b, make_user)
    case_a = _stale_alert(session, project=project_a, resident=resident_a)
    case_b = _stale_alert(session, project=project_b, resident=resident_b)

    result = scheduler.run_once(session_factory=_session_factory)

    assert result.projects_scanned >= 2
    assert result.escalations == 2
    assert result.errors == 0

    # Each case must have a backup_escalated marker.
    for case in (case_a, case_b):
        event = session.exec(
            select(CaseEvent).where(
                CaseEvent.case_id == case.id,
                CaseEvent.event_type == BACKUP_ESCALATED_EVENT,
            )
        ).first()
        assert event is not None, f"case {case.id} was not escalated"


def test_run_once_is_idempotent_across_ticks(
    session: Session, project: Project, make_user
):
    """Two `run_once` passes back-to-back must produce exactly one
    backup_escalated row per case — proves the runner does not
    double-page on the natural cadence (it ticks every few seconds)."""
    resident, _ = _resident_with_user(session, project, make_user)
    _backup_doctor(session, project, make_user)
    case = _stale_alert(session, project=project, resident=resident)

    first = scheduler.run_once(session_factory=_session_factory)
    second = scheduler.run_once(session_factory=_session_factory)

    assert first.escalations == 1
    assert second.escalations == 0, "second pass must not double-escalate"

    events = session.exec(
        select(CaseEvent).where(
            CaseEvent.case_id == case.id,
            CaseEvent.event_type == BACKUP_ESCALATED_EVENT,
        )
    ).all()
    assert len(events) == 1


def test_run_once_requeues_stuck_attempts_across_every_project(
    session: Session, make_user
):
    project_a = Project(name="Project A2", enable_security_desk_alerts=False)
    project_b = Project(name="Project B2", enable_security_desk_alerts=False)
    session.add(project_a)
    session.add(project_b)
    session.commit()
    session.refresh(project_a)
    session.refresh(project_b)

    resident_a, _ = _resident_with_user(session, project_a, make_user)
    resident_b, _ = _resident_with_user(session, project_b, make_user)
    doctor_a = make_user(Role.DOCTOR)
    doctor_b = make_user(Role.DOCTOR)
    stuck_a = _stuck_attempt(
        session, project=project_a, resident=resident_a, recipient=doctor_a
    )
    stuck_b = _stuck_attempt(
        session, project=project_b, resident=resident_b, recipient=doctor_b
    )

    result = scheduler.run_once(session_factory=_session_factory)

    assert result.reaped == 2
    session.refresh(stuck_a)
    session.refresh(stuck_b)
    # Reaper moves sending → queued (and the service then re-attempts
    # delivery; the stub gateway resolves immediately to `sent`).
    assert stuck_a.status in {
        NotificationStatus.QUEUED.value,
        NotificationStatus.SENT.value,
        NotificationStatus.FAILED.value,
    }
    assert stuck_b.status in {
        NotificationStatus.QUEUED.value,
        NotificationStatus.SENT.value,
        NotificationStatus.FAILED.value,
    }


def test_run_once_is_quiet_with_no_work_to_do(session: Session, project: Project):
    """Idle ticks must not write any rows — the runner is on by default
    and we cannot afford log/audit churn when nothing is wrong."""
    result = scheduler.run_once(session_factory=_session_factory)
    assert result.idle is True
    assert result.escalations == 0
    assert result.reaped == 0
    assert result.errors == 0
    # Nothing committed.
    assert (
        session.exec(
            select(CaseEvent).where(
                CaseEvent.event_type == BACKUP_ESCALATED_EVENT
            )
        ).first()
        is None
    )


def test_run_once_isolates_per_project_errors(
    monkeypatch: pytest.MonkeyPatch, session: Session, make_user
):
    """A pathological project (e.g. transient DB error during its
    escalation pass) must NOT halt the other projects' processing.
    The runner counts the error and keeps going."""
    project_ok = Project(name="OK", enable_security_desk_alerts=False)
    project_broken = Project(name="Broken", enable_security_desk_alerts=False)
    session.add(project_ok)
    session.add(project_broken)
    session.commit()
    session.refresh(project_ok)
    session.refresh(project_broken)

    resident_ok, _ = _resident_with_user(session, project_ok, make_user)
    _backup_doctor(session, project_ok, make_user)
    case_ok = _stale_alert(session, project=project_ok, resident=resident_ok)

    broken_id = project_broken.id
    real_escalate = scheduler.escalate_stale_alerts

    def _maybe_throw(session_arg: Session, *, project_id: Any, **kwargs: Any):
        if project_id == broken_id:
            raise RuntimeError("simulated transient db error")
        return real_escalate(session_arg, project_id=project_id, **kwargs)

    monkeypatch.setattr(scheduler, "escalate_stale_alerts", _maybe_throw)

    result = scheduler.run_once(session_factory=_session_factory)

    assert result.errors == 1, "pathological project must be counted"
    assert result.escalations == 1, "other projects must still escalate"

    event = session.exec(
        select(CaseEvent).where(
            CaseEvent.case_id == case_ok.id,
            CaseEvent.event_type == BACKUP_ESCALATED_EVENT,
        )
    ).first()
    assert event is not None


# --------------------------------------------------------------------------- #
# run_loop                                                                    #
# --------------------------------------------------------------------------- #


def test_run_loop_honors_max_iterations(session: Session, project: Project):
    """Test-friendly: the loop exits after `max_iterations` ticks. The
    injected `sleep` lets the test run with no real time passing."""
    sleeps: list[float] = []

    completed = scheduler.run_loop(
        tick_seconds=2,
        max_iterations=3,
        session_factory=_session_factory,
        sleep=sleeps.append,
    )

    assert completed == 3


def test_run_loop_honors_stop_event(session: Session, project: Project):
    """Cooperative shutdown: setting the stop_event preempts the next
    tick's sleep so the loop exits immediately (no waiting out the
    full tick window)."""
    stop = threading.Event()
    stop.set()  # Pre-set: the loop should run zero iterations.

    completed = scheduler.run_loop(
        tick_seconds=2,
        stop_event=stop,
        max_iterations=10,
        session_factory=_session_factory,
        sleep=lambda _: None,
    )

    assert completed == 0


def test_run_loop_calls_runner_once_per_iteration(
    monkeypatch: pytest.MonkeyPatch, session: Session, project: Project
):
    """Sanity: each tick drives a single `run_once` call. Mocked so the
    test asserts the cadence without depending on db state."""
    calls: list[int] = []

    def _fake_run_once(*, session_factory=None, now=None):
        calls.append(1)
        return scheduler.TickResult()

    monkeypatch.setattr(scheduler, "run_once", _fake_run_once)

    completed = scheduler.run_loop(
        tick_seconds=2,
        max_iterations=4,
        session_factory=_session_factory,
        sleep=lambda _: None,
    )

    assert completed == 4
    assert len(calls) == 4


def test_run_loop_survives_run_once_exception(
    monkeypatch: pytest.MonkeyPatch, session: Session, project: Project
):
    """A catastrophic `run_once` failure (DB unreachable, etc.) must be
    swallowed so the runner stays alive for the next tick. This is the
    second safety net above `run_once`'s own per-project try/except."""
    call_count = {"n": 0}

    def _exploding_run_once(*, session_factory=None, now=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("catastrophic")
        return scheduler.TickResult()

    monkeypatch.setattr(scheduler, "run_once", _exploding_run_once)

    completed = scheduler.run_loop(
        tick_seconds=1,
        max_iterations=3,
        sleep=lambda _: None,
    )

    assert completed == 3
    assert call_count["n"] == 3
