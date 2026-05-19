"""Emergency case tables (PLAN.md Slice 5 happy path).

Slice 5 creates an alerted case, logs the initial state event, and records the
push notification attempt. Later slices extend lifecycle/vitals/escalation.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin, utcnow


class EmergencyCase(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "emergency_cases"

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    resident_id: uuid.UUID = Field(foreign_key="residents.id", index=True)
    created_by_user_id: uuid.UUID = Field(foreign_key="users.id")
    assigned_doctor_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")

    status: str = Field(index=True)
    alert_time: datetime = Field(default_factory=utcnow, nullable=False)
    acknowledged_at: datetime | None = Field(default=None)
    on_site_at: datetime | None = Field(default=None)
    closed_at: datetime | None = Field(default=None)

    symptom_codes: list = Field(default_factory=list, sa_column=Column(JSON))
    location_text: str | None = Field(default=None)
    latitude: float | None = Field(default=None)
    longitude: float | None = Field(default=None)
    resolved_outcome: str | None = Field(default=None)


class NotificationAttempt(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "notification_attempts"

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    case_id: uuid.UUID = Field(foreign_key="emergency_cases.id", index=True)
    channel: str = Field(index=True)
    recipient_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    status: str = Field(index=True)
    provider_ref: str | None = Field(default=None)
    error: str | None = Field(default=None)
    attempted_at: datetime = Field(default_factory=utcnow, nullable=False)


class CaseEvent(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "case_events"

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    case_id: uuid.UUID = Field(foreign_key="emergency_cases.id", index=True)
    actor_user_id: uuid.UUID | None = Field(default=None, foreign_key="users.id")
    event_type: str = Field(index=True)
    from_status: str | None = Field(default=None)
    to_status: str | None = Field(default=None)
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))


class DeviceToken(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "device_tokens"

    project_id: uuid.UUID | None = Field(default=None, foreign_key="projects.id", index=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    platform: str = Field(default="web")
    push_token: str = Field(index=True, unique=True)
    disabled_at: datetime | None = Field(default=None)
    last_seen_at: datetime = Field(default_factory=utcnow, nullable=False)
