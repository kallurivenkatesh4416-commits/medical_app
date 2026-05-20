"""Hospital handover PDF tables (PLAN.md Slice 8 / brief §8).

The PDF itself lives in encrypted object storage (same gateway as medical
records, Slice 4). A ``handover_pdfs`` row holds metadata + the storage key;
``handover_dispatches`` records each email/WhatsApp send attempt (one row per
channel, mirroring the durable ``notification_attempts`` outbox pattern from
Slice 6). PHI never leaves the row — outbound messages carry only a 15-minute
signed link, not the body.
"""

import uuid
from datetime import datetime

from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin, utcnow


class HandoverPdf(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "handover_pdfs"

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    case_id: uuid.UUID = Field(foreign_key="emergency_cases.id", index=True)
    generated_by: uuid.UUID = Field(foreign_key="users.id", index=True)
    generated_at: datetime = Field(default_factory=utcnow, nullable=False)
    storage_key: str = Field(unique=True, index=True)
    file_name: str = Field()
    size_bytes: int = Field(default=0)
    # Doctor's name + medical-council registration number captured at the
    # moment of generation — frozen on the PDF, so even if the doctor's
    # profile updates later, the signed handover stays an honest record.
    doctor_name: str = Field()
    doctor_registration_number: str = Field()
    # Receiving facility — typed by the doctor at generation time, no
    # hospital directory yet (that's Phase 2). Free text, plain.
    hospital_destination: str = Field()


class HandoverDispatch(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "handover_dispatches"

    project_id: uuid.UUID = Field(foreign_key="projects.id", index=True)
    handover_id: uuid.UUID = Field(foreign_key="handover_pdfs.id", index=True)
    actor_user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    channel: str = Field(index=True)  # HandoverDispatchChannel
    recipient: str = Field()  # email address OR phone number, plain
    # NotificationStatus reused so the outbox dashboard reads uniformly:
    # queued -> sending -> sent | failed.
    status: str = Field(index=True)
    provider_ref: str | None = Field(default=None)
    error: str | None = Field(default=None)
    attempted_at: datetime = Field(default_factory=utcnow, nullable=False)
