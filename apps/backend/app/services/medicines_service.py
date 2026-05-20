"""Medicine reminders service (PLAN.md Slice 9 / brief §6).

Two PHI surfaces:

- ``MedicineSchedule`` — a resident's prescribed medicine + recurrence.
  Created either by the resident themselves or by a doctor on the
  resident's behalf. Listed by the resident (self) or by staff
  (doctor / nurse / ops) for residents in the same project; staff reads
  are PHI-blocked for builder_admin / security_desk and consent-gated
  via ``EMERGENCY_SHARE_WITH_DOCTOR`` (same pattern as the Slice 4
  records list).
- ``MedicineDoseLog`` — the resident's tap of "I took it" / "I skipped".
  Self-only writes. Staff can read aggregate adherence (taken / skipped /
  missed counts over a window); per-dose history is treated as PHI
  consistent with the staff-PHI surface.

The mobile app schedules **device-local** notifications from the
schedule's ``times_of_day``; no FCM push is required (Slice 9 has no
external-key dependency). The ``MEDICINE_REMINDER_NOTIFICATIONS`` consent
already exists in ``ConsentType`` (Slice 3 onboarding) and gates schedule
creation — a resident who declined reminders cannot have a schedule
created on their account.

``missed`` is computed live, not stored: a slot from a still-active
schedule whose ``scheduled_for`` is more than ``MISSED_GRACE_SECONDS`` in
the past and has no log row is "missed". This keeps the data model
honest (no scheduler yet — that's documented as a future hardening item
in open-questions.md).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.enums import (
    AuditAction,
    ConsentType,
    MedicineDoseStatus,
    MedicineFrequency,
    Role,
)
from app.models.base import utcnow
from app.models.consent import Consent
from app.models.medicine import MedicineDoseLog, MedicineSchedule
from app.models.resident import Resident
from app.models.user import User
from app.services.audit import record_audit
from app.services.auth_service import AuthError
from app.services.residents_service import assert_consent, get_resident_for_user

# How long after a scheduled slot we still consider "in the window". Slots
# older than this with no log are counted as missed in adherence summaries.
MISSED_GRACE_SECONDS = 60 * 60  # 1 hour


@dataclass
class ScheduleInput:
    name: str
    frequency: MedicineFrequency
    times_of_day: list[str]
    start_date: date
    end_date: date | None
    dose: str | None = None
    instructions: str | None = None


@dataclass
class DoseLogInput:
    schedule_id: uuid.UUID
    scheduled_for: datetime
    status: MedicineDoseStatus
    notes: str | None = None


# --------------------------------------------------------------------------- #
# Schedule creation                                                            #
# --------------------------------------------------------------------------- #


def create_schedule(
    session: Session,
    *,
    actor: User,
    resident_id: uuid.UUID,
    data: ScheduleInput,
    from_ip: str | None,
) -> dict:
    """Create a medicine schedule for ``resident_id``.

    Allowed actors:
    - The resident themselves (``actor.role == RESIDENT`` AND
      ``resident.user_id == actor.id``). Acts as self-prescription /
      reminder-only configuration.
    - A doctor in the same project as the resident.

    Both paths require ``MEDICINE_REMINDER_NOTIFICATIONS`` consent on the
    resident (revocation is honoured live — declining reminders means no
    schedule can be created)."""
    resident = _resident_for_actor(session, actor=actor, resident_id=resident_id)
    assert_consent(
        session, resident.id, ConsentType.MEDICINE_REMINDER_NOTIFICATIONS
    )
    _validate_schedule(data)

    schedule = MedicineSchedule(
        project_id=resident.project_id,
        resident_id=resident.id,
        prescribed_by=actor.id,
        name=data.name.strip(),
        dose=(data.dose or None) and data.dose.strip(),
        instructions=(data.instructions or None) and data.instructions.strip(),
        frequency=data.frequency.value,
        times_of_day=list(data.times_of_day),
        start_date=data.start_date,
        end_date=data.end_date,
        active=True,
    )
    session.add(schedule)
    session.flush()
    record_audit(
        session,
        action=AuditAction.MEDICINE_SCHEDULE_CREATED,
        actor_user_id=actor.id,
        project_id=resident.project_id,
        resource_type="medicine_schedule",
        resource_id=str(schedule.id),
        from_ip=from_ip,
        purpose="medicine.schedule_create",
        meta={"resident_id": str(resident.id), "frequency": schedule.frequency},
        commit=False,
    )
    session.commit()
    session.refresh(schedule)
    return serialize_schedule(schedule)


def deactivate_schedule(
    session: Session,
    *,
    actor: User,
    schedule_id: uuid.UUID,
    from_ip: str | None,
    expected_resident_id: uuid.UUID | None = None,
) -> dict:
    """Mark a schedule inactive (the mobile app cancels its local
    notifications on next sync). Doctor or the resident themselves.

    ``expected_resident_id`` is the API path's ``{resident_id}`` segment.
    When supplied (staff route), the schedule's ``resident_id`` MUST match
    — otherwise a doctor in the same project could deactivate resident
    A's schedule through resident B's URL. 404 (no existence leak)
    rather than 403 keeps the response shape consistent with the rest of
    the resident-scoped staff API."""
    schedule = _schedule_for_actor(session, actor=actor, schedule_id=schedule_id)
    if (
        expected_resident_id is not None
        and schedule.resident_id != expected_resident_id
    ):
        raise AuthError(404, "schedule_not_found", "Unknown medicine schedule.")
    if schedule.active:
        schedule.active = False
        session.add(schedule)
    record_audit(
        session,
        action=AuditAction.MEDICINE_SCHEDULE_UPDATED,
        actor_user_id=actor.id,
        project_id=schedule.project_id,
        resource_type="medicine_schedule",
        resource_id=str(schedule.id),
        from_ip=from_ip,
        purpose="medicine.schedule_deactivate",
        meta={"active": False},
        commit=False,
    )
    session.commit()
    session.refresh(schedule)
    return serialize_schedule(schedule)


# --------------------------------------------------------------------------- #
# Listing                                                                      #
# --------------------------------------------------------------------------- #


def list_own_schedules(
    session: Session, *, user: User, from_ip: str | None
) -> list[dict]:
    """Resident's own schedule list — drives the mobile reminder sync.

    Reads ``MEDICINE_REMINDER_NOTIFICATIONS`` live. When the consent is
    revoked we return an **empty list** so the device-local
    ``LocalReminderScheduler.syncFromSchedules`` cancels every reminder on
    the next refresh. The DB rows are not deleted — re-granting consent
    brings them back into view, preserving the resident's intent and the
    historical dose-log audit trail (per DPDP §8: minimum-necessary
    storage that the resident can still reactivate)."""
    resident = get_resident_for_user(session, user)
    record_audit(
        session,
        action=AuditAction.MEDICINE_SCHEDULE_LIST,
        actor_user_id=user.id,
        project_id=resident.project_id,
        resource_type="medicine_schedule",
        from_ip=from_ip,
        purpose="medicine.list_own",
    )
    if not _has_consent(
        session, resident_id=resident.id, consent=ConsentType.MEDICINE_REMINDER_NOTIFICATIONS
    ):
        return []
    rows = _active_or_recent(session, resident_id=resident.id)
    return [serialize_schedule(s) for s in rows]


def list_resident_schedules_as_staff(
    session: Session,
    *,
    actor: User,
    resident_id: uuid.UUID,
    from_ip: str | None,
) -> list[dict]:
    resident = _staff_visible_resident(session, actor=actor, resident_id=resident_id)
    assert_consent(session, resident.id, ConsentType.EMERGENCY_SHARE_WITH_DOCTOR)
    rows = _active_or_recent(session, resident_id=resident.id)
    record_audit(
        session,
        action=AuditAction.MEDICINE_SCHEDULE_LIST,
        actor_user_id=actor.id,
        project_id=resident.project_id,
        resource_type="medicine_schedule",
        resource_id=str(resident.id),
        from_ip=from_ip,
        purpose="medicine.list_staff",
    )
    return [serialize_schedule(s) for s in rows]


def active_schedules_for_resident(
    session: Session, *, resident_id: uuid.UUID
) -> list[MedicineSchedule]:
    """Used by the Slice 8 handover PDF (§6 Current Medicines). No audit
    here — the caller (handover_service.generate_handover) already audits
    HANDOVER_GENERATED with the resident in scope."""
    return list(
        session.exec(
            select(MedicineSchedule)
            .where(
                MedicineSchedule.resident_id == resident_id,
                MedicineSchedule.active.is_(True),  # type: ignore[union-attr]
            )
            .order_by(MedicineSchedule.created_at.desc())  # type: ignore[arg-type]
        ).all()
    )


# --------------------------------------------------------------------------- #
# Dose logging (resident self-only)                                            #
# --------------------------------------------------------------------------- #


def log_own_dose(
    session: Session,
    *,
    user: User,
    data: DoseLogInput,
    from_ip: str | None,
) -> dict:
    """Resident records "I took it" / "I skipped" for one scheduled slot.

    Self-only — only the resident knows. The (schedule_id, scheduled_for)
    pair is uniquely indexed at the DB so a network-flake retry maps to
    the existing row instead of double-logging."""
    if user.role != Role.RESIDENT.value:
        raise AuthError(403, "resident_required", "Only the resident can log doses.")
    if data.status not in {MedicineDoseStatus.TAKEN, MedicineDoseStatus.SKIPPED}:
        raise AuthError(
            422,
            "invalid_dose_status",
            "Dose log status must be 'taken' or 'skipped'.",
        )

    resident = get_resident_for_user(session, user)
    schedule = session.get(MedicineSchedule, data.schedule_id)
    if (
        schedule is None
        or schedule.resident_id != resident.id
        or schedule.project_id != resident.project_id
    ):
        raise AuthError(404, "schedule_not_found", "Unknown medicine schedule.")
    if not schedule.active:
        raise AuthError(
            409, "schedule_inactive", "Schedule is inactive; new doses cannot be logged."
        )
    # Slot integrity: only real schedule slots may be logged, so adherence
    # counts (`taken` + `skipped` over `scheduled_slots`) cannot exceed 100%
    # and the mobile app cannot poison the doctor's view by submitting an
    # off-clock timestamp. AS_NEEDED skips this check by design — PRN
    # medicines record actual intake times, not scheduled slots, and never
    # contribute to `missed`.
    if not _is_valid_dose_slot(schedule, data.scheduled_for):
        raise AuthError(
            422,
            "invalid_dose_slot",
            "scheduled_for must match one of this schedule's time-of-day slots on a valid date.",
        )

    # Pre-check: a duplicate retry on the same slot must return the
    # existing log row, not a 409 to the device. The race-safety net for
    # truly concurrent inserts is still the unique index +
    # IntegrityError handler below.
    existing = session.exec(
        select(MedicineDoseLog).where(
            MedicineDoseLog.schedule_id == schedule.id,
            MedicineDoseLog.scheduled_for == data.scheduled_for,
        )
    ).first()
    if existing is not None:
        return serialize_dose_log(existing)

    log = MedicineDoseLog(
        project_id=resident.project_id,
        schedule_id=schedule.id,
        resident_id=resident.id,
        scheduled_for=data.scheduled_for,
        status=data.status.value,
        logged_by=user.id,
        notes=data.notes,
    )
    session.add(log)
    try:
        # Flush the insert first so a race with a concurrent inserter raises
        # IntegrityError BEFORE we write the audit row — otherwise the audit
        # row commits and the dose log doesn't, breaking the audit contract.
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.exec(
            select(MedicineDoseLog).where(
                MedicineDoseLog.schedule_id == schedule.id,
                MedicineDoseLog.scheduled_for == data.scheduled_for,
            )
        ).first()
        if existing is None:
            raise AuthError(
                409, "dose_log_conflict", "Could not record dose."
            ) from None
        return serialize_dose_log(existing)

    record_audit(
        session,
        action=AuditAction.MEDICINE_DOSE_LOGGED,
        actor_user_id=user.id,
        project_id=resident.project_id,
        resource_type="medicine_dose_log",
        resource_id=str(log.id),
        from_ip=from_ip,
        purpose="medicine.log_dose",
        meta={
            "schedule_id": str(schedule.id),
            "status": log.status,
            "scheduled_for": data.scheduled_for.isoformat(),
        },
        commit=False,
    )
    session.commit()
    session.refresh(log)
    return serialize_dose_log(log)


# --------------------------------------------------------------------------- #
# Adherence summary (staff read; PHI but no per-dose detail leaked beyond     #
# the doctor/nurse/ops PHI surface already established in earlier slices)    #
# --------------------------------------------------------------------------- #


def adherence_summary_for_staff(
    session: Session,
    *,
    actor: User,
    resident_id: uuid.UUID,
    days: int,
    from_ip: str | None,
) -> dict:
    """Return per-schedule taken/skipped counts and a computed-missed count
    over the last ``days`` days. PHI surface: doctor/nurse/ops only,
    tenant-isolated, ``EMERGENCY_SHARE_WITH_DOCTOR`` consent-gated."""
    if days <= 0 or days > 90:
        raise AuthError(
            422, "invalid_window", "Adherence window must be 1-90 days."
        )
    resident = _staff_visible_resident(session, actor=actor, resident_id=resident_id)
    assert_consent(session, resident.id, ConsentType.EMERGENCY_SHARE_WITH_DOCTOR)

    now = utcnow()
    window_start = now - timedelta(days=days)
    schedules = session.exec(
        select(MedicineSchedule).where(
            MedicineSchedule.resident_id == resident.id,
        )
    ).all()
    logs = session.exec(
        select(MedicineDoseLog).where(
            MedicineDoseLog.resident_id == resident.id,
            MedicineDoseLog.scheduled_for >= window_start,
        )
    ).all()

    per_schedule: list[dict] = []
    totals = {"taken": 0, "skipped": 0, "missed": 0, "scheduled_slots": 0}
    for schedule in schedules:
        sched_logs = [log for log in logs if log.schedule_id == schedule.id]
        taken = sum(1 for log in sched_logs if log.status == MedicineDoseStatus.TAKEN.value)
        skipped = sum(
            1 for log in sched_logs if log.status == MedicineDoseStatus.SKIPPED.value
        )
        # Missed = past slots in the window with no log and outside the grace.
        slots = _expand_slots(schedule, window_start=window_start, now=now)
        logged_slots = {log.scheduled_for for log in sched_logs}
        missed = sum(
            1
            for slot in slots
            if slot not in logged_slots
            and (now - slot).total_seconds() > MISSED_GRACE_SECONDS
        )
        per_schedule.append(
            {
                "schedule_id": schedule.id,
                "name": schedule.name,
                "frequency": schedule.frequency,
                "active": schedule.active,
                "taken": taken,
                "skipped": skipped,
                "missed": missed,
                "scheduled_slots": len(slots),
            }
        )
        totals["taken"] += taken
        totals["skipped"] += skipped
        totals["missed"] += missed
        totals["scheduled_slots"] += len(slots)

    record_audit(
        session,
        action=AuditAction.MEDICINE_ADHERENCE_READ,
        actor_user_id=actor.id,
        project_id=resident.project_id,
        resource_type="resident",
        resource_id=str(resident.id),
        from_ip=from_ip,
        purpose="medicine.adherence",
        meta={"days": days},
    )
    return {
        "resident_id": resident.id,
        "window_days": days,
        "totals": totals,
        "schedules": per_schedule,
    }


# --------------------------------------------------------------------------- #
# Serialization                                                                #
# --------------------------------------------------------------------------- #


def serialize_schedule(s: MedicineSchedule) -> dict:
    return {
        "id": s.id,
        "resident_id": s.resident_id,
        "project_id": s.project_id,
        "prescribed_by": s.prescribed_by,
        "name": s.name,
        "dose": s.dose,
        "instructions": s.instructions,
        "frequency": s.frequency,
        "times_of_day": s.times_of_day,
        "start_date": s.start_date,
        "end_date": s.end_date,
        "active": s.active,
        "created_at": s.created_at,
    }


def serialize_dose_log(log: MedicineDoseLog) -> dict:
    return {
        "id": log.id,
        "schedule_id": log.schedule_id,
        "resident_id": log.resident_id,
        "scheduled_for": log.scheduled_for,
        "status": log.status,
        "logged_at": log.logged_at,
        "logged_by": log.logged_by,
        "notes": log.notes,
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


_FREQUENCY_SLOT_COUNTS: dict[str, int] = {
    MedicineFrequency.ONCE_DAILY.value: 1,
    MedicineFrequency.TWICE_DAILY.value: 2,
    MedicineFrequency.THRICE_DAILY.value: 3,
    MedicineFrequency.FOUR_TIMES_DAILY.value: 4,
    MedicineFrequency.WEEKLY.value: 1,
    MedicineFrequency.AS_NEEDED.value: 0,
}


def _validate_schedule(data: ScheduleInput) -> None:
    if not (data.name and data.name.strip()):
        raise AuthError(422, "name_required", "Medicine name is required.")
    if data.end_date is not None and data.end_date < data.start_date:
        raise AuthError(
            422, "invalid_date_range", "end_date must be on or after start_date."
        )
    expected = _FREQUENCY_SLOT_COUNTS.get(data.frequency.value)
    if expected is None:
        raise AuthError(422, "invalid_frequency", "Unknown medicine frequency.")
    # AS_NEEDED (PRN) explicitly has no fixed slots; passing any would
    # cause the mobile reminder scheduler to register fixed-time
    # notifications for a medicine that's supposed to be taken only on
    # need. Reject so the contract stays honest end-to-end.
    if expected == 0 and data.times_of_day:
        raise AuthError(
            422,
            "as_needed_no_slots",
            f"{data.frequency.value} schedules must not declare time-of-day slots.",
        )
    if expected > 0 and len(data.times_of_day) != expected:
        raise AuthError(
            422,
            "times_of_day_mismatch",
            f"{data.frequency.value} expects exactly {expected} time-of-day slot(s).",
        )
    for slot in data.times_of_day:
        if not _is_hhmm(slot):
            raise AuthError(
                422,
                "invalid_time_of_day",
                "times_of_day entries must be 'HH:MM' (24-hour).",
            )
    # Duplicate slots would register the same local reminder twice on the
    # mobile device — the resident would get a double notification and a
    # double dose-log row (different `scheduled_for` second-level
    # timestamps, but conceptually the same slot).
    if len(set(data.times_of_day)) != len(data.times_of_day):
        raise AuthError(
            422,
            "duplicate_time_of_day",
            "times_of_day must not contain duplicate slots.",
        )


def _is_hhmm(value: str) -> bool:
    try:
        hour, minute = value.split(":")
        return 0 <= int(hour) <= 23 and 0 <= int(minute) <= 59
    except (ValueError, AttributeError):
        return False


def _has_consent(
    session: Session, *, resident_id: uuid.UUID, consent: ConsentType
) -> bool:
    """Non-raising read of live consent state. Used where the right answer
    on revocation is a filtered/empty response (the resident's own
    medicine list when reminders are paused) rather than a 403."""
    row = session.exec(
        select(Consent).where(
            Consent.resident_id == resident_id,
            Consent.consent_type == consent.value,
        )
    ).first()
    return row is not None and row.granted


def _resident_for_actor(
    session: Session, *, actor: User, resident_id: uuid.UUID
) -> Resident:
    """Resolve a resident that the actor is allowed to manage schedules for.

    - Resident: only their own profile (404 on mismatch — no existence
      leak via the schedule API).
    - Doctor: any resident in the same project (404 cross-project).
    """
    resident = session.get(Resident, resident_id)
    if resident is None:
        raise AuthError(404, "resident_not_found", "Unknown resident.")
    if actor.role == Role.RESIDENT.value:
        if resident.user_id != actor.id:
            raise AuthError(404, "resident_not_found", "Unknown resident.")
        return resident
    if actor.role == Role.DOCTOR.value:
        if actor.project_id != resident.project_id:
            raise AuthError(404, "resident_not_found", "Unknown resident.")
        return resident
    raise AuthError(
        403, "schedule_role_forbidden", "Only the resident or a doctor can create schedules."
    )


def _schedule_for_actor(
    session: Session, *, actor: User, schedule_id: uuid.UUID
) -> MedicineSchedule:
    schedule = session.get(MedicineSchedule, schedule_id)
    if schedule is None:
        raise AuthError(404, "schedule_not_found", "Unknown medicine schedule.")
    if actor.role == Role.RESIDENT.value:
        resident = get_resident_for_user(session, actor)
        if schedule.resident_id != resident.id:
            raise AuthError(404, "schedule_not_found", "Unknown medicine schedule.")
        return schedule
    if actor.role == Role.DOCTOR.value:
        if schedule.project_id != actor.project_id:
            raise AuthError(404, "schedule_not_found", "Unknown medicine schedule.")
        return schedule
    raise AuthError(
        403, "schedule_role_forbidden", "Only the resident or a doctor can modify schedules."
    )


def _staff_visible_resident(
    session: Session, *, actor: User, resident_id: uuid.UUID
) -> Resident:
    """Staff read-side resolver: tenant-isolated 404 (no existence leak)."""
    resident = session.get(Resident, resident_id)
    if resident is None or actor.project_id != resident.project_id:
        raise AuthError(404, "resident_not_found", "Unknown resident.")
    return resident


def _active_or_recent(
    session: Session, *, resident_id: uuid.UUID
) -> list[MedicineSchedule]:
    """Schedules a clinician or resident usually wants to see: active ones
    plus anything stopped in the last 30 days (so a recent stop is still
    visible without polluting with ancient history)."""
    cutoff = utcnow() - timedelta(days=30)
    rows = session.exec(
        select(MedicineSchedule)
        .where(MedicineSchedule.resident_id == resident_id)
        .order_by(MedicineSchedule.created_at.desc())  # type: ignore[arg-type]
    ).all()
    return [
        r for r in rows if r.active or (r.updated_at and r.updated_at >= cutoff)
    ]


def _is_valid_dose_slot(schedule: MedicineSchedule, when: datetime) -> bool:
    """Return True iff ``when`` is a real slot for ``schedule``:

    - ``AS_NEEDED`` (PRN) accepts any timestamp inside the schedule's
      validity window — there are no fixed clock slots, so the resident
      records the actual intake time.
    - All other frequencies require ``when.strftime('%H:%M')`` to be one
      of the schedule's ``times_of_day`` AND, for ``WEEKLY``, the same
      weekday as the schedule's start_date.
    - The date must lie between ``start_date`` and ``end_date`` (when set).

    The slot equality is exact (no skew tolerance) because the mobile app
    constructs ``scheduled_for`` from the schedule's own ``times_of_day``
    — any off-clock value indicates either a buggy client or tampering."""
    when_date = when.date()
    if when_date < schedule.start_date:
        return False
    if schedule.end_date is not None and when_date > schedule.end_date:
        return False
    if schedule.frequency == MedicineFrequency.AS_NEEDED.value:
        return True
    slot_str = when.strftime("%H:%M")
    if slot_str not in (schedule.times_of_day or []):
        return False
    if schedule.frequency == MedicineFrequency.WEEKLY.value:
        return when_date.weekday() == schedule.start_date.weekday()
    return True


def _expand_slots(
    schedule: MedicineSchedule, *, window_start: datetime, now: datetime
) -> list[datetime]:
    """Project a schedule into concrete datetime slots inside the window.

    AS_NEEDED has no slots (never auto-marked missed). WEEKLY uses the
    schedule's ``start_date`` weekday. All others fan out the daily
    ``times_of_day`` across each day in the window."""
    if schedule.frequency == MedicineFrequency.AS_NEEDED.value:
        return []
    if not schedule.times_of_day:
        return []

    start = max(window_start.date(), schedule.start_date)
    end = min(now.date(), schedule.end_date or now.date())
    if end < start:
        return []

    slots: list[datetime] = []
    if schedule.frequency == MedicineFrequency.WEEKLY.value:
        weekday = schedule.start_date.weekday()
        day = start
        while day <= end:
            if day.weekday() == weekday:
                slots.extend(_combine(day, schedule.times_of_day))
            day = day + timedelta(days=1)
        return slots

    day = start
    while day <= end:
        slots.extend(_combine(day, schedule.times_of_day))
        day = day + timedelta(days=1)
    return slots


def _combine(day: date, times_of_day: list[str]) -> list[datetime]:
    out: list[datetime] = []
    for slot in times_of_day:
        try:
            hour, minute = slot.split(":")
            out.append(datetime.combine(day, time(int(hour), int(minute))))
        except (ValueError, AttributeError):
            continue
    return out
