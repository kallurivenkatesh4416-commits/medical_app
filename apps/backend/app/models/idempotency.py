"""Idempotency keys (brief §10: resource-creating writes accept an
Idempotency-Key). A successful create records its key so a retried request
returns the original outcome instead of creating a duplicate.

We store the created `user_id` (a reference), never response tokens — tokens
are secrets and must not sit at rest here.
"""

import uuid
from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.models.base import UUIDPKMixin, utcnow


class IdempotencyKey(UUIDPKMixin, table=True):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("endpoint", "key", name="uq_idempotency_endpoint_key"),
    )

    endpoint: str = Field(index=True)
    key: str = Field(index=True)
    user_id: uuid.UUID | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
