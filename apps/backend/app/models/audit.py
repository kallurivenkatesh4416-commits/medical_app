"""Append-only audit log (brief §2.3 / §13). Records who/what/when/from_ip/
purpose for every sensitive action. Immutability is enforced two ways:

1. App level: a SQLAlchemy event blocks UPDATE/DELETE of AuditLog (works on
   SQLite too, so tests cover it).
2. DB level: the Slice 2 migration installs a Postgres rule blocking
   UPDATE/DELETE on the table (defence in depth in prod).

No updated_at — the row never changes.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.models.base import UUIDPKMixin, utcnow


class AuditLog(UUIDPKMixin, table=True):
    __tablename__ = "audit_log"

    actor_user_id: uuid.UUID | None = Field(default=None, index=True)
    project_id: uuid.UUID | None = Field(default=None, index=True)
    action: str = Field(index=True)
    resource_type: str | None = Field(default=None)
    resource_id: str | None = Field(default=None)
    from_ip: str | None = Field(default=None)
    purpose: str | None = Field(default=None)
    # Minimal, non-PHI context only (e.g. hashed phone, ids).
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow, nullable=False, index=True)
