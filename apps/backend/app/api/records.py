"""Medical records API (PLAN.md Slice 4).

Uploads, lists, and signed links are PHI surfaces: role-gated, tenant-isolated,
consent-gated for staff, and audited in the service layer.
"""

import hashlib
import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, File, Form, Header, Request, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import get_settings
from app.enums import Role
from app.models.medical_record import MedicalRecord
from app.models.user import User
from app.security.deps import client_ip, forbid_phi_roles, get_db, require_roles
from app.security.jwt import TokenError, decode_record_url_token
from app.services import idempotency, records_service
from app.services.auth_service import AuthError
from app.services.residents_service import get_resident_for_user
from app.services.storage import (
    StorageError,
    get_storage_gateway,
    safe_download_name,
    signed_url_ttl_seconds,
)

router = APIRouter(prefix="/api/v1", tags=["records"])

resident_only = require_roles(Role.RESIDENT)
staff_only = require_roles(Role.DOCTOR, Role.NURSE, Role.OPS)
upload_file_param = File(...)
record_type_param = Form(...)
record_date_param = Form(default=None)
source_param = Form(default=None)
tags_param = Form(default=None)
idempotency_key_header = Header(default=None, alias="Idempotency-Key")


class RecordOut(BaseModel):
    id: uuid.UUID
    file_name: str
    content_type: str
    record_type: str
    record_date: date | None
    source: str | None
    tags: list[str]
    size_bytes: int
    created_at: datetime


class LinkOut(BaseModel):
    url: str
    expires_in_seconds: int


def _parse_tags(raw: str | None) -> list[str]:
    if raw is None or not raw.strip():
        return []
    raw = raw.strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthError(
                422,
                "invalid_tags",
                "Tags must be a JSON list or comma-separated text.",
            ) from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise AuthError(422, "invalid_tags", "Tags must be a list of text values.")
        return [item.strip() for item in parsed if item.strip()]
    return [item.strip() for item in raw.split(",") if item.strip()]


def _ctx_for_upload(
    *,
    key: str,
    user: User,
    file_name: str,
    content_type: str,
    data: bytes,
    record_type: str,
    record_date: date | None,
    source: str | None,
    tags: list[str],
) -> idempotency.IdemContext:
    if len(key) > 128:
        raise AuthError(422, "invalid_idempotency_key", "Idempotency-Key is too long.")
    payload = {
        "file_name": file_name,
        "content_type": content_type,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "record_type": record_type,
        "record_date": record_date.isoformat() if record_date else None,
        "source": source,
        "tags": tags,
    }
    return idempotency.IdemContext(
        key=key,
        owner_fp=idempotency.owner_fingerprint(user.phone),
        request_fp=idempotency.request_fingerprint(payload),
    )


def _conflict() -> AuthError:
    return AuthError(
        409,
        "idempotency_key_conflict",
        "This Idempotency-Key was used with a different request or account.",
    )


def _record_for_replay(
    session: Session, row: idempotency.IdempotencyKey, *, resident_id: uuid.UUID
) -> MedicalRecord | None:
    if row.resource_id is None:
        return None
    rec = session.get(MedicalRecord, row.resource_id)
    if rec is None or rec.deleted_at is not None:
        raise AuthError(404, "record_not_found", "Unknown record.")
    if rec.resident_id != resident_id:
        raise AuthError(404, "record_not_found", "Unknown record.")
    return rec


def _check_upload_replay(
    session: Session, ctx: idempotency.IdemContext, *, resident_id: uuid.UUID
) -> MedicalRecord | None:
    try:
        row = idempotency.check_replay(session, idempotency.RECORDS_ENDPOINT, ctx)
    except idempotency.IdempotencyConflict as exc:
        raise _conflict() from exc
    if row is None:
        return None
    return _record_for_replay(session, row, resident_id=resident_id)


@router.post("/me/records", response_model=RecordOut)
async def upload_my_record(
    request: Request,
    file: UploadFile = upload_file_param,
    record_type: str = record_type_param,
    record_date: date | None = record_date_param,
    source: str | None = source_param,
    tags: str | None = tags_param,
    idempotency_key: str | None = idempotency_key_header,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> dict:
    resident = get_resident_for_user(session, user)
    limit = get_settings().max_upload_bytes + 1
    data = await file.read(limit)
    file_name = safe_download_name(file.filename or "record")
    content_type = file.content_type or "application/octet-stream"
    parsed_tags = _parse_tags(tags)

    ctx: idempotency.IdemContext | None = None
    if idempotency_key:
        ctx = _ctx_for_upload(
            key=idempotency_key,
            user=user,
            file_name=file_name,
            content_type=content_type,
            data=data,
            record_type=record_type,
            record_date=record_date,
            source=source,
            tags=parsed_tags,
        )
        replay = _check_upload_replay(session, ctx, resident_id=resident.id)
        if replay is not None:
            return records_service.serialize_record(replay)

    try:
        record = records_service.upload_record(
            session,
            uploader=user,
            resident=resident,
            file_name=file_name,
            content_type=content_type,
            data=data,
            record_type=record_type,
            record_date=record_date,
            source=source,
            tags=parsed_tags,
            from_ip=client_ip(request),
            idem_ctx=ctx,
        )
    except IntegrityError as exc:
        session.rollback()
        if ctx is not None:
            replay = _check_upload_replay(session, ctx, resident_id=resident.id)
            if replay is not None:
                return records_service.serialize_record(replay)
        raise AuthError(409, "record_upload_conflict", "Record upload conflicted.") from exc
    except StorageError as exc:
        raise AuthError(502, "storage_error", "Could not store medical record.") from exc
    return records_service.serialize_record(record)


@router.get("/me/records", response_model=list[RecordOut])
def list_my_records(
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> list[dict]:
    return records_service.list_own(session, user=user, from_ip=client_ip(request))


@router.get("/me/records/{record_id}/link", response_model=LinkOut)
def link_my_record(
    record_id: uuid.UUID,
    request: Request,
    user: User = Depends(resident_only),
    session: Session = Depends(get_db),
) -> LinkOut:
    url = records_service.issue_link_own(
        session, user=user, record_id=record_id, from_ip=client_ip(request)
    )
    return LinkOut(url=url, expires_in_seconds=signed_url_ttl_seconds())


@router.get(
    "/residents/{resident_id}/records",
    response_model=list[RecordOut],
    dependencies=[Depends(forbid_phi_roles)],
)
def list_resident_records(
    resident_id: uuid.UUID,
    request: Request,
    actor: User = Depends(staff_only),
    session: Session = Depends(get_db),
) -> list[dict]:
    return records_service.list_for_staff(
        session, actor=actor, resident_id=resident_id, from_ip=client_ip(request)
    )


@router.get(
    "/residents/{resident_id}/records/{record_id}/link",
    response_model=LinkOut,
    dependencies=[Depends(forbid_phi_roles)],
)
def link_resident_record(
    resident_id: uuid.UUID,
    record_id: uuid.UUID,
    request: Request,
    actor: User = Depends(staff_only),
    session: Session = Depends(get_db),
) -> LinkOut:
    url = records_service.issue_link_staff(
        session,
        actor=actor,
        resident_id=resident_id,
        record_id=record_id,
        from_ip=client_ip(request),
    )
    return LinkOut(url=url, expires_in_seconds=signed_url_ttl_seconds())


@router.get("/records/download/{token}")
def download_record(token: str, session: Session = Depends(get_db)) -> Response:
    try:
        storage_key, download_name = decode_record_url_token(token)
    except TokenError as exc:
        raise AuthError(401, "invalid_record_link", "Record link is invalid or expired.") from exc

    rec = session.exec(
        select(MedicalRecord).where(
            MedicalRecord.storage_key == storage_key,
            MedicalRecord.deleted_at.is_(None),  # type: ignore[union-attr]
        )
    ).first()
    if rec is None:
        raise AuthError(404, "record_not_found", "Unknown record.")

    try:
        data = get_storage_gateway().get_bytes(key=storage_key)
    except StorageError as exc:
        raise AuthError(404, "record_file_missing", "Record file is unavailable.") from exc

    name = safe_download_name(download_name or rec.file_name)
    return Response(
        content=data,
        media_type=rec.content_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
