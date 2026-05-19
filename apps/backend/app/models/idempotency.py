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
    # Non-reversible fingerprint of the verified phone that created this key.
    # Replay is refused unless the caller proves the SAME phone (prevents one
    # subject reusing another subject's key to obtain their tokens).
    owner_fp: str = Field()
    # Fingerprint of the original request body; a same-key replay with a
    # different payload is a conflict, not a replay.
    request_fp: str = Field()
    # Reference to the created resource. user_id for onboarding; resource_id
    # for other resource-creating writes (e.g. medical records).
    user_id: uuid.UUID | None = Field(default=None)
    resource_id: uuid.UUID | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
