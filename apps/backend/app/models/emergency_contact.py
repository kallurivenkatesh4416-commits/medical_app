"""Emergency contacts for a resident (min 1, max 3 — enforced in the
onboarding service, brief §3 / docs/onboarding-flow.md)."""

import uuid

from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class EmergencyContact(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "emergency_contacts"

    resident_id: uuid.UUID = Field(foreign_key="residents.id", index=True)
    name: str = Field()
    phone: str = Field()
    relation: str | None = Field(default=None)
    is_primary: bool = Field(default=False)
    priority: int = Field(default=0)
