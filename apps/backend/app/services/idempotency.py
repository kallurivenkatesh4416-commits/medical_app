"""Idempotency helper (brief §10). Stores only the created user_id (never
tokens) plus an owner fingerprint and a request fingerprint.

Owner binding is a security control: a replay is honoured only if the caller
proves the SAME verified phone that created the key — otherwise one subject
could reuse another's key and receive their session.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlmodel import Session, select

from app.models.idempotency import IdempotencyKey
from app.security.hashing import hash_secret

ONBOARDING_ENDPOINT = "onboarding.complete"


def owner_fingerprint(phone: str) -> str:
    """Non-reversible reference to the verified phone (keyed HMAC)."""
    return hash_secret(f"idem-owner:{phone}")[:32]


def request_fingerprint(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class IdemContext:
    key: str
    owner_fp: str
    request_fp: str


class IdempotencyConflict(Exception):
    """Same key presented by a different owner or with a different body."""


def find(session: Session, endpoint: str, key: str) -> IdempotencyKey | None:
    return session.exec(
        select(IdempotencyKey).where(
            IdempotencyKey.endpoint == endpoint, IdempotencyKey.key == key
        )
    ).first()


def check_replay(
    session: Session, endpoint: str, ctx: IdemContext
) -> IdempotencyKey | None:
    """Return the stored row IFF this exact owner+request may replay it.
    Raises IdempotencyConflict if the key belongs to someone else or the
    body differs."""
    row = find(session, endpoint, ctx.key)
    if row is None:
        return None
    if row.owner_fp != ctx.owner_fp or row.request_fp != ctx.request_fp:
        raise IdempotencyConflict
    return row


def stage(
    session: Session,
    *,
    endpoint: str,
    ctx: IdemContext,
    user_id: uuid.UUID | None = None,
    resource_id: uuid.UUID | None = None,
) -> IdempotencyKey:
    """Add (no commit) — caller commits with the rest of the transaction.
    `user_id` is used by onboarding; `resource_id` by other creates."""
    row = IdempotencyKey(
        endpoint=endpoint,
        key=ctx.key,
        owner_fp=ctx.owner_fp,
        request_fp=ctx.request_fp,
        user_id=user_id,
        resource_id=resource_id,
    )
    session.add(row)
    session.flush()
    return row


RECORDS_ENDPOINT = "records.upload"
