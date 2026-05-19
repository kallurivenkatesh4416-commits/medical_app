"""Medical profile — 1:1 with a resident (brief §6). Lists are JSON; this is
PHI and only reachable via PHI-guarded, audited endpoints."""

import uuid

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class MedicalProfile(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "medical_profiles"

    resident_id: uuid.UUID = Field(foreign_key="residents.id", unique=True, index=True)
    blood_group: str | None = Field(default=None)
    diseases: list = Field(default_factory=list, sa_column=Column(JSON))
    allergies: list = Field(default_factory=list, sa_column=Column(JSON))
    surgeries: list = Field(default_factory=list, sa_column=Column(JSON))
    preferred_hospital: str | None = Field(default=None)
    insurance: dict = Field(default_factory=dict, sa_column=Column(JSON))
