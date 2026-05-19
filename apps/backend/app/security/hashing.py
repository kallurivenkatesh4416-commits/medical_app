"""Keyed hashing for secrets stored at rest (OTP codes, refresh tokens).

Raw secrets are never persisted (brief §13). HMAC-SHA256 keyed with the app
secret; comparisons are constant-time.
"""

import hashlib
import hmac
import secrets

from app.config import get_settings


def _key() -> bytes:
    return get_settings().jwt_secret.encode()


def hash_secret(value: str) -> str:
    return hmac.new(_key(), value.encode(), hashlib.sha256).hexdigest()


def verify_secret(value: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_secret(value), hashed)


def new_opaque_token() -> str:
    """A high-entropy opaque token (used for refresh tokens)."""
    return secrets.token_urlsafe(48)


def new_numeric_otp(length: int) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))
