"""Resident — extends a user with flat/villa + demographics (brief §6).
Tenant-scoped by project_id."""

import uuid
from datetime import date

from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class Resident(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "residents"

    user_id: uuid.UUID = Field(foreign_key="users.id", unique=True, index=True)
    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    flat_villa_number: str = Field()
    dob: date = Field()
    gender: str = Field()
