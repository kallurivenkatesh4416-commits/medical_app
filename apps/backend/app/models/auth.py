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


class RefreshToken(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "refresh_tokens"

    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True)
    expires_at: datetime = Field()
    revoked_at: datetime | None = Field(default=None)
