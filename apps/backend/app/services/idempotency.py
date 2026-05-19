"""Tiny idempotency helper (brief §10). Stores only the created user_id, never
response tokens. Records are staged inside the caller's transaction so the
side effect and its key commit atomically."""

import uuid

from sqlmodel import Session, select

from app.models.idempotency import IdempotencyKey

ONBOARDING_ENDPOINT = "onboarding.complete"


def find(session: Session, endpoint: str, key: str) -> IdempotencyKey | None:
    return session.exec(
        select(IdempotencyKey).where(
            IdempotencyKey.endpoint == endpoint, IdempotencyKey.key == key
        )
    ).first()


def stage(
    session: Session, *, endpoint: str, key: str, user_id: uuid.UUID
) -> IdempotencyKey:
    """Add (no commit) — caller commits with the rest of the transaction."""
    row = IdempotencyKey(endpoint=endpoint, key=key, user_id=user_id)
    session.add(row)
    session.flush()
    return row
