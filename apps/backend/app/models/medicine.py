"""Medicine reminders (PLAN.md Slice 9 / brief §6).

Tables:

- ``medicine_schedules`` — a resident's prescribed medicine + when to take
  it. Times-of-day are a JSON list of ``"HH:MM"`` strings; the mobile app
  uses them to schedule device-local notifications (no FCM — see Slice 9
  handoff). PHI.
- ``medicine_dose_logs`` — one row per resident-acted-on dose (taken or
  skipped). Only the resident's tap creates a row; ``missed`` is computed
  live from schedule + logs in the adherence query (no scheduler yet).
  Audit-adjacent: every log writes ``MEDICINE_DOSE_LOGGED`` to ``audit_log``.

Both tables are project-scoped so the same tenant guards from Slices 3 / 7 /
8 apply unchanged.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin, utcnow


class MedicineSchedule(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "medicine_schedules"

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    resident_id: uuid.UUID = Field(foreign_key="residents.id", index=True)
    # Who prescribed / set this schedule. For now: a doctor (acting on behalf
    # of the resident) or the resident themselves. Identity is frozen on the
    # row so a later doctor profile change doesn't rewrite history.
    prescribed_by: uuid.UUID = Field(foreign_key="users.id", index=True)
    name: str = Field()
    dose: str | None = Field(default=None)  # e.g. "5 mg", "1 tablet"
    instructions: str | None = Field(default=None)  # "after food", "with water"
    frequency: str = Field(index=True)  # MedicineFrequency
    # JSON list of "HH:MM" 24-hour strings (UTC project time at MVP). Empty
    # list is valid for AS_NEEDED.
    times_of_day: list = Field(default_factory=list, sa_column=Column(JSON))
    start_date: date = Field()
    end_date: date | None = Field(default=None)  # nullable = ongoing
    active: bool = Field(default=True, index=True)


class MedicineDoseLog(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "medicine_dose_logs"
    # UNIQUE INDEX (not a table-level UniqueConstraint) so the migration
    # works on SQLite — see 0009_medicine_reminders.py for the rationale.
    __table_args__ = (
        Index(
            "uq_medicine_dose_logs_schedule_slot",
            "schedule_id",
            "scheduled_for",
            unique=True,
        ),
    )

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    schedule_id: uuid.UUID = Field(foreign_key="medicine_schedules.id", index=True)
    # Denormalised resident_id for the staff adherence query (no join needed
    # when scanning a resident's history).
    resident_id: uuid.UUID = Field(foreign_key="residents.id", index=True)
    # The dose slot this log corresponds to — combining the schedule's
    # time-of-day with a calendar date. Used to detect duplicate logs and to
    # compute "missed" (no log for a past slot).
    scheduled_for: datetime = Field(index=True)
    status: str = Field(index=True)  # MedicineDoseStatus (taken | skipped)
    # The mobile tap timestamp. ``scheduled_for - logged_at`` lets the
    # doctor see "logged 20 minutes late".
    logged_at: datetime = Field(default_factory=utcnow, nullable=False)
    # Resident self-log is the only path today; ``logged_by`` is the user
    # who tapped (always the resident) so the audit trail is honest.
    logged_by: uuid.UUID = Field(foreign_key="users.id")
    notes: str | None = Field(default=None)
