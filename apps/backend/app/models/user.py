"""User — auth identity + role (brief §6). Soft-delete supports DPDP deletion
(30-day purge job lands with the patient-data slices)."""

import uuid
from datetime import datetime

from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class User(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "users"

    # Nullable: super_admin is global (not bound to one project).
    project_id: uuid.UUID | None = Field(
        default=None, foreign_key="projects.id", index=True
    )
    phone: str = Field(index=True, unique=True)
    # Stores a canonical Role value (validated against app.enums.Role).
    role: str = Field(index=True)
    full_name: str | None = Field(default=None)
    is_active: bool = Field(default=True)
    # Soft-delete (DPDP); a 30-day purge job removes the row later.
    deleted_at: datetime | None = Field(default=None)
