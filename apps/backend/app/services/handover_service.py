"""Hospital handover PDF service (PLAN.md Slice 8 / brief §8).

Aggregates a case's PHI surface, renders a 1-2 page form-style PDF via
Jinja2 -> xhtml2pdf, stores the bytes through the StorageGateway (encrypted
at rest), and persists a ``handover_pdfs`` row + ``case_events`` ``handover_*``
+ append-only audit_log entries on every generate / link / dispatch /
download. The PDF body itself is **never embedded** in outbound mail or
WhatsApp — only a 15-minute signed link is shared, so DPDP revocation /
expiry stays enforceable (brief §2.3, hard-capped in storage.py).

WeasyPrint is named in PLAN.md §4, but its GTK/Pango runtime requirement is
impractical for Windows dev and adds a native dep on CI. docs/setup.md records
the swap to xhtml2pdf — same Jinja2 template, pure-Python renderer, sufficient
for §8's form-style layout. Output PDFs are tested for §8 contents via pypdf.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime  # datetime kept for type hints
from io import BytesIO
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import update
from sqlmodel import Session, select
from xhtml2pdf import pisa

from app.config import get_settings
from app.enums import (
    AuditAction,
    CaseNoteType,
    ConsentType,
    HandoverDispatchChannel,
    NotificationStatus,
    Role,
)
from app.models.base import utcnow
from app.models.emergency import CaseEvent, CaseNote, CaseVital, EmergencyCase
from app.models.emergency_contact import EmergencyContact
from app.models.handover import HandoverDispatch, HandoverPdf
from app.models.medical_profile import MedicalProfile
from app.models.medical_record import MedicalRecord
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User
from app.security.jwt import (
    TokenError,
    create_handover_url_token,
    decode_handover_url_token,
)
from app.services import medicines_service
from app.services.audit import record_audit
from app.services.auth_service import AuthError
from app.services.email import get_email_gateway
from app.services.notifications import get_notification_gateway
from app.services.residents_service import assert_consent
from app.services.storage import (
    StorageError,
    get_storage_gateway,
    safe_download_name,
    signed_url_ttl_seconds,
)

HANDOVER_GENERATED_EVENT = "handover_generated"
HANDOVER_DISPATCHED_EVENT = "handover_dispatched"

_TEMPLATE_DIR = Path(__file__).parent / "handover_templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


@dataclass
class HandoverInput:
    hospital_destination: str
    doctor_registration_number: str
    doctor_assessment: str | None = None


@dataclass
class DispatchInput:
    email: str | None
    whatsapp: str | None


# --------------------------------------------------------------------------- #
# Generate                                                                     #
# --------------------------------------------------------------------------- #


def generate_handover(
    session: Session,
    *,
    actor: User,
    case_id: uuid.UUID,
    data: HandoverInput,
    from_ip: str | None,
) -> dict:
    """Aggregate the case, render the PDF, upload to storage, persist + audit.

    Doctor-only — the PDF carries the doctor's name + registration number into
    a clinical handover and the brief calls out a Telemedicine consultation
    record (§2.4)."""
    if actor.role != Role.DOCTOR.value:
        raise AuthError(403, "doctor_required", "Only a doctor can generate the handover PDF.")
    if not actor.full_name:
        raise AuthError(
            422,
            "doctor_name_missing",
            "Doctor must have a full name on file before generating a handover.",
        )
    hospital = (data.hospital_destination or "").strip()
    registration = (data.doctor_registration_number or "").strip()
    if not hospital:
        raise AuthError(422, "hospital_required", "Hospital destination is required.")
    if not registration:
        raise AuthError(
            422,
            "registration_required",
            "Doctor medical-council registration number is required.",
        )

    case = _case_for_doctor(session, actor=actor, case_id=case_id)
    # Brief §6 / DPDP: hospital handover is a separate PHI surface; the
    # resident's EMERGENCY_SHARE_WITH_HOSPITAL consent gates both rendering
    # and any link reissue (revocation is honoured immediately — assert_consent
    # reads live state). The PHI never reaches storage if consent is missing.
    assert_consent(session, case.resident_id, ConsentType.EMERGENCY_SHARE_WITH_HOSPITAL)
    context = _aggregate(
        session,
        case=case,
        hospital_destination=hospital,
        doctor=actor,
        doctor_registration_number=registration,
        doctor_assessment=data.doctor_assessment,
    )
    pdf_bytes = _render_pdf(context)

    storage_key = uuid.uuid4().hex
    file_name = f"handover-{str(case.id)[:8]}-{utcnow():%Y%m%d-%H%M}.pdf"
    try:
        get_storage_gateway().put_encrypted(
            key=storage_key, data=pdf_bytes, content_type="application/pdf"
        )
    except StorageError as exc:
        raise AuthError(502, "storage_error", "Could not store handover PDF.") from exc

    handover = HandoverPdf(
        project_id=case.project_id,
        case_id=case.id,
        generated_by=actor.id,
        storage_key=storage_key,
        file_name=safe_download_name(file_name),
        size_bytes=len(pdf_bytes),
        doctor_name=actor.full_name,
        doctor_registration_number=registration,
        hospital_destination=hospital,
    )
    session.add(handover)
    session.flush()
    session.add(
        CaseEvent(
            project_id=case.project_id,
            case_id=case.id,
            actor_user_id=actor.id,
            event_type=HANDOVER_GENERATED_EVENT,
            from_status=case.status,
            to_status=case.status,
            meta={
                "handover_id": str(handover.id),
                "hospital_destination": hospital,
                "size_bytes": len(pdf_bytes),
            },
        )
    )
    record_audit(
        session,
        action=AuditAction.HANDOVER_GENERATED,
        actor_user_id=actor.id,
        project_id=case.project_id,
        resource_type="handover_pdf",
        resource_id=str(handover.id),
        from_ip=from_ip,
        purpose="handover.generate",
        meta={"case_id": str(case.id), "hospital_destination": hospital},
        commit=False,
    )
    session.commit()
    session.refresh(handover)
    return serialize_handover(handover, signed_url=_signed_url(handover))


def issue_link(
    session: Session, *, actor: User, handover_id: uuid.UUID, from_ip: str | None
) -> dict:
    """Mint a fresh ≤15-minute signed link without re-rendering the PDF.
    Doctor/nurse/ops can re-share an already-generated handover."""
    handover = _handover_for_staff(session, actor=actor, handover_id=handover_id)
    # If the resident revoked EMERGENCY_SHARE_WITH_HOSPITAL after the handover
    # was generated, a re-share would push PHI to a now-unconsented surface.
    # Reading consent live blocks that path (the existing PDF stays on storage
    # for retention but cannot be re-shared via a fresh signed URL).
    case = session.get(EmergencyCase, handover.case_id)
    if case is not None:
        assert_consent(
            session, case.resident_id, ConsentType.EMERGENCY_SHARE_WITH_HOSPITAL
        )
    record_audit(
        session,
        action=AuditAction.HANDOVER_LINK_ISSUED,
        actor_user_id=actor.id,
        project_id=handover.project_id,
        resource_type="handover_pdf",
        resource_id=str(handover.id),
        from_ip=from_ip,
        purpose="handover.link",
    )
    return {
        "handover_id": handover.id,
        "url": _signed_url(handover),
        "expires_in_seconds": signed_url_ttl_seconds(),
    }


# --------------------------------------------------------------------------- #
# Dispatch — email + WhatsApp; one failing must not block the other            #
# --------------------------------------------------------------------------- #


def dispatch_handover(
    session: Session,
    *,
    actor: User,
    handover_id: uuid.UUID,
    data: DispatchInput,
    from_ip: str | None,
) -> dict:
    if actor.role != Role.DOCTOR.value:
        raise AuthError(403, "doctor_required", "Only a doctor can dispatch the handover PDF.")
    email = (data.email or "").strip() or None
    whatsapp = (data.whatsapp or "").strip() or None
    if not email and not whatsapp:
        raise AuthError(
            422,
            "recipient_required",
            "Provide at least one of: email, whatsapp.",
        )

    handover = _handover_for_staff(session, actor=actor, handover_id=handover_id)
    # DPDP: dispatch is the moment PHI leaves the platform; gate it on live
    # EMERGENCY_SHARE_WITH_HOSPITAL consent so revocation between generate and
    # dispatch is honoured.
    case = session.get(EmergencyCase, handover.case_id)
    if case is not None:
        assert_consent(
            session, case.resident_id, ConsentType.EMERGENCY_SHARE_WITH_HOSPITAL
        )
    signed = _signed_url(handover)
    subject = f"{get_settings().handover_email_subject_prefix} — {handover.hospital_destination}"
    body = _dispatch_body(handover=handover, signed_url=signed)

    dispatches: list[HandoverDispatch] = []
    if email:
        dispatches.append(
            _dispatch_one(
                session,
                handover=handover,
                actor=actor,
                channel=HandoverDispatchChannel.EMAIL,
                recipient=email,
                subject=subject,
                body=body,
            )
        )
    if whatsapp:
        dispatches.append(
            _dispatch_one(
                session,
                handover=handover,
                actor=actor,
                channel=HandoverDispatchChannel.WHATSAPP,
                recipient=whatsapp,
                subject=subject,
                body=body,
            )
        )

    session.add(
        CaseEvent(
            project_id=handover.project_id,
            case_id=handover.case_id,
            actor_user_id=actor.id,
            event_type=HANDOVER_DISPATCHED_EVENT,
            from_status=None,
            to_status=None,
            meta={
                "handover_id": str(handover.id),
                "channels": [d.channel for d in dispatches],
                "results": [
                    {"channel": d.channel, "status": d.status, "error": d.error}
                    for d in dispatches
                ],
            },
        )
    )
    record_audit(
        session,
        action=AuditAction.HANDOVER_DISPATCHED,
        actor_user_id=actor.id,
        project_id=handover.project_id,
        resource_type="handover_pdf",
        resource_id=str(handover.id),
        from_ip=from_ip,
        purpose="handover.dispatch",
        meta={
            "channels": [d.channel for d in dispatches],
            "results": [
                {"channel": d.channel, "status": d.status, "error": d.error}
                for d in dispatches
            ],
        },
        commit=False,
    )
    session.commit()
    return {
        "handover_id": handover.id,
        "dispatches": [serialize_dispatch(d) for d in dispatches],
    }


def _dispatch_one(
    session: Session,
    *,
    handover: HandoverPdf,
    actor: User,
    channel: HandoverDispatchChannel,
    recipient: str,
    subject: str,
    body: str,
) -> HandoverDispatch:
    """Send one dispatch as a durable outbox step (mirrors the Slice 6
    notification_attempts pattern):

    1. Persist a ``queued`` row in its own commit so a crash here leaves the
       row in the outbox (a future reaper can resume it).
    2. Atomically claim ``queued -> sending`` in its own commit. A crash
       between this and the provider returning leaves a ``sending`` row —
       the documented "stuck claim" tradeoff (losing one notification beats
       double-sending; see open-questions.md).
    3. Call the provider.
    4. Persist ``sent`` / ``failed`` (with error class name) in its own
       commit; one channel's exception must never block the other.
    """
    dispatch = HandoverDispatch(
        project_id=handover.project_id,
        handover_id=handover.id,
        actor_user_id=actor.id,
        channel=channel.value,
        recipient=recipient,
        status=NotificationStatus.QUEUED.value,
    )
    session.add(dispatch)
    session.commit()
    session.refresh(dispatch)

    # Atomic claim queued -> sending. If two callers ever race here (Slice 6's
    # original use case), only one wins. We just inserted, so the claim is
    # expected to succeed; the structure mirrors Slice 6's `_claim_attempt`.
    claim = session.execute(
        update(HandoverDispatch)
        .where(
            HandoverDispatch.id == dispatch.id,
            HandoverDispatch.status == NotificationStatus.QUEUED.value,
        )
        .values(status=NotificationStatus.SENDING.value)
    )
    session.commit()
    if claim.rowcount != 1:
        session.refresh(dispatch)
        return dispatch
    session.refresh(dispatch)

    try:
        if channel is HandoverDispatchChannel.EMAIL:
            dispatch.provider_ref = get_email_gateway().send(
                to=recipient, subject=subject, body=body
            )
        else:
            dispatch.provider_ref = get_notification_gateway().send_whatsapp(
                to=recipient, body=body
            )
        dispatch.status = NotificationStatus.SENT.value
    except Exception as exc:  # one channel failing must not block the other
        dispatch.status = NotificationStatus.FAILED.value
        dispatch.error = exc.__class__.__name__
    session.add(dispatch)
    session.commit()
    return dispatch


# --------------------------------------------------------------------------- #
# Download — token-gated, no auth required (capability link)                   #
# --------------------------------------------------------------------------- #


def fetch_pdf_for_token(
    session: Session, *, token: str, from_ip: str | None
) -> tuple[HandoverPdf, bytes, str]:
    try:
        handover_id, storage_key, name = decode_handover_url_token(token)
    except TokenError as exc:
        raise AuthError(
            401, "invalid_handover_link", "Handover link is invalid or expired."
        ) from exc
    handover = session.get(HandoverPdf, handover_id)
    if handover is None or handover.storage_key != storage_key:
        raise AuthError(404, "handover_not_found", "Unknown handover.")
    try:
        data = get_storage_gateway().get_bytes(key=storage_key)
    except StorageError as exc:
        raise AuthError(404, "handover_file_missing", "Handover file is unavailable.") from exc
    record_audit(
        session,
        action=AuditAction.HANDOVER_DOWNLOADED,
        actor_user_id=None,  # capability link — no authenticated actor
        project_id=handover.project_id,
        resource_type="handover_pdf",
        resource_id=str(handover.id),
        from_ip=from_ip,
        purpose="handover.download",
    )
    return handover, data, name or handover.file_name


# --------------------------------------------------------------------------- #
# Serialization                                                                #
# --------------------------------------------------------------------------- #


def serialize_handover(handover: HandoverPdf, *, signed_url: str | None = None) -> dict:
    out = {
        "id": handover.id,
        "case_id": handover.case_id,
        "project_id": handover.project_id,
        "generated_by": handover.generated_by,
        "generated_at": handover.generated_at,
        "file_name": handover.file_name,
        "size_bytes": handover.size_bytes,
        "doctor_name": handover.doctor_name,
        "doctor_registration_number": handover.doctor_registration_number,
        "hospital_destination": handover.hospital_destination,
    }
    if signed_url is not None:
        out["signed_url"] = signed_url
        out["expires_in_seconds"] = signed_url_ttl_seconds()
    return out


def serialize_dispatch(dispatch: HandoverDispatch) -> dict:
    return {
        "id": dispatch.id,
        "handover_id": dispatch.handover_id,
        "channel": dispatch.channel,
        "recipient": dispatch.recipient,
        "status": dispatch.status,
        "provider_ref": dispatch.provider_ref,
        "error": dispatch.error,
        "attempted_at": dispatch.attempted_at,
    }


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _case_for_doctor(
    session: Session, *, actor: User, case_id: uuid.UUID
) -> EmergencyCase:
    case = session.exec(
        select(EmergencyCase).where(
            EmergencyCase.id == case_id,
            EmergencyCase.project_id == actor.project_id,
        )
    ).first()
    if case is None:
        raise AuthError(404, "case_not_found", "Unknown emergency case.")
    return case


def _handover_for_staff(
    session: Session, *, actor: User, handover_id: uuid.UUID
) -> HandoverPdf:
    handover = session.get(HandoverPdf, handover_id)
    if handover is None or handover.project_id != actor.project_id:
        raise AuthError(404, "handover_not_found", "Unknown handover.")
    return handover


def _signed_url(handover: HandoverPdf) -> str:
    """Return a ≤15-minute signed download URL for the handover PDF.

    In ``provider_mode='stub'`` we emit a backend-proxied capability link
    (`/api/v1/handover/file/{token}`) — the token is ``handover_url``-typed
    (distinct from `record_url`) and binds the storage key, so the proxy
    audits each download. In ``provider_mode='live'`` the S3 gateway returns
    a native presigned GET that bypasses our backend; that path mirrors the
    Slice 4 records gateway and is the **only** way live storage exposes
    bytes (``S3StorageGateway.get_bytes`` raises by design — see
    storage.py). Bucket-side access logs cover live audit; we still audit
    LINK_ISSUED at issue time."""
    settings = get_settings()
    if settings.provider_mode == "live":
        return get_storage_gateway().signed_url(
            key=handover.storage_key, download_name=handover.file_name
        )
    ttl = signed_url_ttl_seconds()
    token = create_handover_url_token(
        handover_id=handover.id,
        storage_key=handover.storage_key,
        download_name=handover.file_name,
        ttl=ttl,
    )
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/v1/handover/file/{token}"


def _dispatch_body(*, handover: HandoverPdf, signed_url: str) -> str:
    return (
        f"Hospital handover for case {str(handover.case_id)[:8]} "
        f"(destination: {handover.hospital_destination}).\n\n"
        f"Open the PDF (expires in {signed_url_ttl_seconds() // 60} minutes):\n"
        f"{signed_url}\n\n"
        "This link is short-lived for patient privacy. Refresh it from the "
        "dashboard if it expires."
    )


def _aggregate(
    session: Session,
    *,
    case: EmergencyCase,
    hospital_destination: str,
    doctor: User,
    doctor_registration_number: str,
    doctor_assessment: str | None,
) -> dict:
    """Pull every §8 field into a render-ready dict. No PHI leaves this dict
    — it stays in-process until the PDF bytes are written to encrypted
    storage."""
    project = session.get(Project, case.project_id)
    resident = session.get(Resident, case.resident_id)
    resident_user = session.get(User, resident.user_id) if resident else None
    profile = (
        session.exec(
            select(MedicalProfile).where(MedicalProfile.resident_id == resident.id)
        ).first()
        if resident
        else None
    )
    contacts = (
        session.exec(
            select(EmergencyContact)
            .where(EmergencyContact.resident_id == resident.id)
            .order_by(EmergencyContact.priority)  # type: ignore[arg-type]
        ).all()
        if resident
        else []
    )
    primary = next((c for c in contacts if c.is_primary), contacts[0] if contacts else None)
    vitals_rows = session.exec(
        select(CaseVital)
        .where(CaseVital.case_id == case.id)
        .order_by(CaseVital.recorded_at.desc())  # type: ignore[arg-type]
    ).all()
    notes_rows = session.exec(
        select(CaseNote)
        .where(CaseNote.case_id == case.id)
        .order_by(CaseNote.created_at.desc())  # type: ignore[arg-type]
    ).all()
    records_rows = (
        session.exec(
            select(MedicalRecord)
            .where(
                MedicalRecord.resident_id == resident.id,
                MedicalRecord.deleted_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(MedicalRecord.created_at.desc())  # type: ignore[arg-type]
        ).all()
        if resident
        else []
    )

    observations = [n for n in notes_rows if n.note_type == CaseNoteType.OBSERVATION.value]
    treatments = [n for n in notes_rows if n.note_type == CaseNoteType.TREATMENT.value]
    escalations = [
        n for n in notes_rows if n.note_type == CaseNoteType.ESCALATION_REASON.value
    ]

    generated_at = utcnow()
    short_case_id = str(case.id)[:8]
    spo2_trend = [v.spo2_percent for v in vitals_rows if v.spo2_percent is not None]

    return {
        "project_name": project.name if project else "—",
        "case_id": str(case.id),
        "short_case_id": short_case_id,
        "generated_at_str": generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        "patient": {
            "name": resident_user.full_name if resident_user else None,
            "age": _age_from_dob(resident.dob) if resident else None,
            "gender": resident.gender if resident else None,
            "blood_group": profile.blood_group if profile else None,
            "flat_villa_number": resident.flat_villa_number if resident else None,
        },
        "complaint": {
            "symptoms": ", ".join(case.symptom_codes or []) or None,
            "assessment": (doctor_assessment or "").strip() or None,
        },
        "timeline": {
            "alert_time": _fmt(case.alert_time),
            "acknowledged_at": _fmt(case.acknowledged_at),
            "en_route_at": _fmt(case.en_route_at),
            "on_site_at": _fmt(case.on_site_at),
            "escalated_at": _fmt(case.escalated_at),
        },
        "vitals": [_vital_dict(v) for v in vitals_rows],
        "vitals_sparkline": _sparkline(spo2_trend) if len(spo2_trend) > 1 else None,
        "history": {
            "diseases": ", ".join(profile.diseases) if profile and profile.diseases else None,
            "allergies": ", ".join(profile.allergies) if profile and profile.allergies else None,
            "surgeries": ", ".join(profile.surgeries) if profile and profile.surgeries else None,
        },
        # §6 Current Medicines — populated from active medicine_schedules for
        # the resident (Slice 9 wire-in). The template iterates `m.name` /
        # `m.dose` / `m.schedule`; keep those field names if either side
        # changes. An empty list still renders the section header with a
        # "no current medicines on file" placeholder.
        "medicines": (
            [_medicine_dict(s) for s in medicines_service.active_schedules_for_resident(
                session, resident_id=resident.id
            )]
            if resident is not None
            else []
        ),
        "observations": [_note_dict(n) for n in observations],
        "treatments": [_note_dict(n) for n in treatments],
        "escalations": [_note_dict(n) for n in escalations],
        "family": {
            "name": primary.name if primary else None,
            "phone": primary.phone if primary else None,
            "relation": primary.relation if primary else None,
        },
        "records": [_record_dict(r) for r in records_rows],
        "doctor": {
            "name": doctor.full_name or "",
            "registration_number": doctor_registration_number,
        },
        "hospital_destination": hospital_destination,
    }


def _render_pdf(context: dict) -> bytes:
    html = _jinja.get_template("handover.html.j2").render(**context)
    buf = BytesIO()
    status = pisa.CreatePDF(src=html, dest=buf, encoding="utf-8")
    if status.err:
        raise AuthError(502, "pdf_render_failed", "Could not render handover PDF.")
    return buf.getvalue()


def _fmt(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else None


def _age_from_dob(dob: date | None) -> int | None:
    if dob is None:
        return None
    today = date.today()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return max(0, years)


def _vital_dict(v: CaseVital) -> dict:
    return {
        "recorded_at_str": v.recorded_at.strftime("%Y-%m-%d %H:%M"),
        "bp_systolic": v.blood_pressure_systolic,
        "bp_diastolic": v.blood_pressure_diastolic,
        "spo2": v.spo2_percent,
        "heart_rate": v.heart_rate_bpm,
        "respiratory_rate": v.respiratory_rate_bpm,
        "temperature": v.temperature_c,
    }


def _note_dict(n: CaseNote) -> dict:
    return {
        "body": n.body,
        "advice_given": n.advice_given,
        "recorded_at_str": n.created_at.strftime("%Y-%m-%d %H:%M"),
    }


def _record_dict(r: MedicalRecord) -> dict:
    return {
        "file_name": r.file_name,
        "record_type": r.record_type,
        "uploaded_at_str": r.created_at.strftime("%Y-%m-%d"),
    }


def _medicine_dict(s) -> dict:  # noqa: ANN001 - MedicineSchedule
    """Render a MedicineSchedule for the handover §6 template. The
    template expects `name` / `dose` / `schedule` — we fold frequency +
    times_of_day into a single human-readable `schedule` string so the
    PDF stays compact."""
    times = ", ".join(s.times_of_day) if s.times_of_day else None
    parts = [s.frequency.replace("_", " ")]
    if times:
        parts.append(f"at {times}")
    if s.instructions:
        parts.append(s.instructions)
    return {
        "name": s.name,
        "dose": s.dose or "",
        "schedule": " — ".join(parts),
    }


def _sparkline(values: list[int]) -> str:
    """ASCII trend (newest first) so the §8 'sparkline if multiple readings'
    requirement renders inside xhtml2pdf without inline images. Brief asks
    for a sparkline; the receiving clinician gets the trend at a glance."""
    if not values:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    span = max(1, hi - lo)
    return " ".join(blocks[min(7, (v - lo) * 7 // span)] for v in values)
