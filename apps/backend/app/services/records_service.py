"""Medical record upload / listing / signed links (PLAN.md Slice 4).

Every action is audited. Staff access is PHI-role-blocked (by the dependency),
tenant-isolated, and consent-gated, mirroring residents_service.
"""

import uuid
from datetime import date

from sqlmodel import Session, select

from app.enums import AuditAction, ConsentType, MedicalRecordType
from app.models.medical_record import MedicalRecord
from app.models.resident import Resident
from app.models.user import User
from app.services import idempotency
from app.services.audit import record_audit
from app.services.auth_service import AuthError
from app.services.residents_service import assert_consent, get_resident_for_user
from app.services.storage import get_storage_gateway, safe_download_name

ALLOWED_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}


def _validate(content_type: str, size: int, record_type: str) -> None:
    from app.config import get_settings

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise AuthError(415, "unsupported_type", "Only PDF, JPEG or PNG is accepted.")
    if size <= 0 or size > get_settings().max_upload_bytes:
        raise AuthError(413, "file_too_large", "File is empty or exceeds the size limit.")
    if record_type not in {t.value for t in MedicalRecordType}:
        raise AuthError(422, "invalid_record_type", "Unknown record type.")


def serialize_record(r: MedicalRecord) -> dict:
    return {
        "id": r.id,
        "file_name": r.file_name,
        "content_type": r.content_type,
        "record_type": r.record_type,
        "record_date": r.record_date,
        "source": r.source,
        "tags": r.tags,
        "size_bytes": r.size_bytes,
        "created_at": r.created_at,
    }


def upload_record(
    session: Session,
    *,
    uploader: User,
    resident: Resident,
    file_name: str,
    content_type: str,
    data: bytes,
    record_type: str,
    record_date: date | None,
    source: str | None,
    tags: list[str],
    from_ip: str | None,
    idem_ctx: idempotency.IdemContext | None = None,
) -> MedicalRecord:
    _validate(content_type, len(data), record_type)
    file_name = safe_download_name(file_name)

    # Server-generated opaque key (no user input -> no path/key injection).
    storage_key = uuid.uuid4().hex
    # Write the encrypted blob first; a later DB failure only orphans a blob
    # (harmless, swept by future GC) — it never leaves a row without its file.
    get_storage_gateway().put_encrypted(
        key=storage_key, data=data, content_type=content_type
    )

    record = MedicalRecord(
        resident_id=resident.id,
        project_id=resident.project_id,
        storage_key=storage_key,
        file_name=file_name,
        content_type=content_type,
        record_type=record_type,
        record_date=record_date,
        source=source,
        tags=tags,
        size_bytes=len(data),
        uploaded_by=uploader.id,
    )
    session.add(record)
    session.flush()

    record_audit(
        session,
        action=AuditAction.RECORD_UPLOADED,
        actor_user_id=uploader.id,
        project_id=resident.project_id,
        resource_type="medical_record",
        resource_id=str(record.id),
        from_ip=from_ip,
        purpose="records.upload",
        meta={"record_type": record_type},
        commit=False,
    )
    if idem_ctx is not None:
        idempotency.stage(
            session,
            endpoint=idempotency.RECORDS_ENDPOINT,
            ctx=idem_ctx,
            resource_id=record.id,
        )

    session.commit()
    session.refresh(record)
    return record


def _live_records(session: Session, resident_id: uuid.UUID) -> list[MedicalRecord]:
    return list(
        session.exec(
            select(MedicalRecord)
            .where(
                MedicalRecord.resident_id == resident_id,
                MedicalRecord.deleted_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(MedicalRecord.created_at.desc())  # type: ignore[union-attr]
        ).all()
    )


def list_own(
    session: Session, *, user: User, from_ip: str | None
) -> list[dict]:
    resident = get_resident_for_user(session, user)
    record_audit(
        session,
        action=AuditAction.RECORD_LIST,
        actor_user_id=user.id,
        project_id=resident.project_id,
        resource_type="resident",
        resource_id=str(resident.id),
        from_ip=from_ip,
        purpose="self.records_list",
    )
    return [serialize_record(r) for r in _live_records(session, resident.id)]


def _staff_resident(
    session: Session, actor: User, resident_id: uuid.UUID
) -> Resident:
    resident = session.get(Resident, resident_id)
    if resident is None or actor.project_id != resident.project_id:
        raise AuthError(404, "resident_not_found", "Unknown resident.")
    assert_consent(session, resident.id, ConsentType.EMERGENCY_SHARE_WITH_DOCTOR)
    return resident


def list_for_staff(
    session: Session, *, actor: User, resident_id: uuid.UUID, from_ip: str | None
) -> list[dict]:
    resident = _staff_resident(session, actor, resident_id)
    record_audit(
        session,
        action=AuditAction.RECORD_LIST,
        actor_user_id=actor.id,
        project_id=resident.project_id,
        resource_type="resident",
        resource_id=str(resident.id),
        from_ip=from_ip,
        purpose="staff.records_list",
    )
    return [serialize_record(r) for r in _live_records(session, resident.id)]


def _get_record(session: Session, record_id: uuid.UUID) -> MedicalRecord:
    rec = session.get(MedicalRecord, record_id)
    if rec is None or rec.deleted_at is not None:
        raise AuthError(404, "record_not_found", "Unknown record.")
    return rec


def issue_link_own(
    session: Session, *, user: User, record_id: uuid.UUID, from_ip: str | None
) -> str:
    resident = get_resident_for_user(session, user)
    rec = _get_record(session, record_id)
    if rec.resident_id != resident.id:
        raise AuthError(404, "record_not_found", "Unknown record.")
    return _issue_link(session, rec, actor=user, from_ip=from_ip, purpose="self.records_link")


def issue_link_staff(
    session: Session,
    *,
    actor: User,
    resident_id: uuid.UUID,
    record_id: uuid.UUID,
    from_ip: str | None,
) -> str:
    resident = _staff_resident(session, actor, resident_id)
    rec = _get_record(session, record_id)
    if rec.resident_id != resident.id:
        raise AuthError(404, "record_not_found", "Unknown record.")
    return _issue_link(
        session, rec, actor=actor, from_ip=from_ip, purpose="staff.records_link"
    )


def _issue_link(
    session: Session,
    rec: MedicalRecord,
    *,
    actor: User,
    from_ip: str | None,
    purpose: str,
) -> str:
    url = get_storage_gateway().signed_url(
        key=rec.storage_key, download_name=rec.file_name
    )
    record_audit(
        session,
        action=AuditAction.RECORD_LINK_ISSUED,
        actor_user_id=actor.id,
        project_id=rec.project_id,
        resource_type="medical_record",
        resource_id=str(rec.id),
        from_ip=from_ip,
        purpose=purpose,
    )
    return url
