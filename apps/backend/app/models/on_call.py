"""On-call schedule (PLAN.md Slice 6 / data-model refinements).

Fan-out resolves the active primary recipient for a role; the 60s no-ack timer
escalates to the active ``is_backup=true`` row. ``contact_phone`` overrides the
user's phone for SMS/voice when set (a duty line rather than a personal number).
"""

import uuid
from datetime import datetime

from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class OnCallSchedule(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "on_call_schedules"

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    # One of Role.{DOCTOR,NURSE,OPS,SECURITY_DESK} (validated in the service).
    role: str = Field(index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    starts_at: datetime = Field(index=True)
    ends_at: datetime = Field(index=True)
    is_backup: bool = Field(default=False, index=True)
    # Duty line for SMS/voice; falls back to users.phone when unset.
    contact_phone: str | None = Field(default=None)
