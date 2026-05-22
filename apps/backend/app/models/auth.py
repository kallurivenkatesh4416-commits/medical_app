"""OTP codes + refresh tokens. Secrets are stored hashed at rest, never raw
(brief §13)."""

import uuid
from datetime import datetime

from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class OtpCode(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "otp_codes"

    phone: str = Field(index=True)
    code_hash: str = Field()
    expires_at: datetime = Field()
    consumed_at: datetime | None = Field(default=None)
    attempts: int = Field(default=0)


class OtpAttempt(UUIDPKMixin, TimestampMixin, table=True):
    """Rate-limit ledger. Phone is stored only as a keyed fingerprint."""

    __tablename__ = "otp_attempts"

    phone_fp: str = Field(index=True)
    from_ip: str | None = Field(default=None, index=True)
    kind: str = Field(index=True)
    attempted_at: datetime = Field(index=True)


class RefreshToken(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "refresh_tokens"

    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True)
    expires_at: datetime = Field()
    revoked_at: datetime | None = Field(default=None)
