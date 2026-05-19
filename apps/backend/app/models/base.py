"""Shared model mixins. Every table has a UUID id + created_at/updated_at;
tenant-scoped tables also carry project_id (brief §6, PLAN.md data model)."""

import uuid
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    # Naive UTC (columns are tz-naive DateTime); avoids the deprecated
    # datetime.utcnow() while keeping a single source of "now".
    return datetime.now(UTC).replace(tzinfo=None)


class UUIDPKMixin(SQLModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)


class TimestampMixin(SQLModel):
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": utcnow},
    )
