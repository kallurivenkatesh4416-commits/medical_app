"""Granular consent (brief §2.3, PLAN.md). One current-state row per
(resident, consent_type); every grant/revoke/re-consent also writes audit_log.
`policy_version` lets a policy change force re-consent."""

import uuid
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class Consent(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "consents"
    __table_args__ = (
        UniqueConstraint("resident_id", "consent_type", name="uq_consent_resident_type"),
    )

    resident_id: uuid.UUID = Field(foreign_key="residents.id", index=True)
    consent_type: str = Field(index=True)
    granted: bool = Field()
    policy_version: str = Field()
    granted_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)
