"""Uploaded medical record (brief §6). The file lives in object storage
(encrypted); this row holds only metadata + the storage key. PHI — reachable
only via PHI-guarded, audited endpoints. Soft-delete supports DPDP."""

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class MedicalRecord(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "medical_records"

    resident_id: uuid.UUID = Field(foreign_key="residents.id", index=True)
    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    storage_key: str = Field(unique=True, index=True)
    file_name: str = Field()
    content_type: str = Field()
    record_type: str = Field(index=True)  # MedicalRecordType
    record_date: date | None = Field(default=None)
    source: str | None = Field(default=None)
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    size_bytes: int = Field(default=0)
    uploaded_by: uuid.UUID = Field(foreign_key="users.id")
    deleted_at: datetime | None = Field(default=None)
