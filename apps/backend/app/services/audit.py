"""Audit service (brief §2.3 / §13). Append-only writes only — never updates
or deletes (the model + a Postgres rule enforce that)."""

import uuid

from sqlmodel import Session

from app.enums import AuditAction
from app.models.audit import AuditLog
from app.security.hashing import hash_secret


def phone_fingerprint(phone: str) -> str:
    """Non-reversible phone reference for audit meta (avoids storing raw PII)."""
    return hash_secret(phone)[:16]


def record_audit(
    session: Session,
    *,
    action: AuditAction,
    actor_user_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    from_ip: str | None = None,
    purpose: str | None = None,
    meta: dict | None = None,
    commit: bool = True,
) -> AuditLog:
    entry = AuditLog(
        action=action.value,
        actor_user_id=actor_user_id,
        project_id=project_id,
        resource_type=resource_type,
        resource_id=resource_id,
        from_ip=from_ip,
        purpose=purpose,
        meta=meta or {},
    )
    session.add(entry)
    if commit:
        # Default: audit is its own durable write.
        session.commit()
        session.refresh(entry)
    else:
        # Caller batches this into a larger atomic transaction (e.g. onboarding).
        session.flush()
    return entry
