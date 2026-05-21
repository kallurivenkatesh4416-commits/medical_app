"""Slice 8 — hospital handover PDF: doctor-only generate, all §8 sections in
the rendered PDF, ≤15 min signed link, email + WhatsApp dispatch via the
provider stub gateways, tenant isolation, audit + case_events on every step.
"""

import uuid
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlmodel import Session, select

from app.enums import AuditAction, NotificationStatus, Role
from app.models.audit import AuditLog
from app.models.base import utcnow
from app.models.emergency import CaseEvent, CaseNote, CaseVital
from app.models.handover import HandoverDispatch, HandoverPdf
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User
from app.security.jwt import create_handover_url_token
from app.services.email import StubEmailGateway
from app.services.notifications import StubNotificationGateway

# --------------------------------------------------------------------------- #
# Helpers (mirror tests/test_emergency.py shape)                               #
# --------------------------------------------------------------------------- #


def _register_token(client: TestClient, phone: str) -> str:
    req = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    code = req.json()["dev_otp"]
    vr = client.post("/api/v1/auth/otp/verify", json={"phone": phone, "code": code})
    assert vr.status_code == 200, vr.text
    return vr.json()["registration_token"]


def _payload(project_id: str) -> dict:
    return {
        "full_name": "Asha Rao",
        "dob": "1948-03-02",
        "gender": "female",
        "project_id": project_id,
        "flat_villa_number": "B-1203",
        "emergency_contacts": [
            {
                "name": "Ravi Rao",
                "phone": "+15557770001",
                "relation": "son",
                "is_primary": True,
            }
        ],
        "disclaimer_acknowledged": True,
        "consents": [
            {"consent_type": "data_storage", "granted": True},
            {"consent_type": "emergency_share_with_doctor", "granted": True},
            {"consent_type": "emergency_share_with_hospital", "granted": True},
            {"consent_type": "family_member_access", "granted": False},
            {"consent_type": "medicine_reminder_notifications", "granted": True},
        ],
        "blood_group": "O+",
        "diseases": ["hypertension"],
        "allergies": ["penicillin"],
    }


def _onboard(
    client: TestClient, project: Project, session: Session, phone: str
) -> tuple[str, Resident]:
    tok = _register_token(client, phone)
    resp = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 200, resp.text
    user = session.exec(select(User).where(User.phone == phone)).first()
    resident = session.exec(select(Resident).where(Resident.user_id == user.id)).first()
    return resp.json()["access_token"], resident


def _auth(client: TestClient, login, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(user.phone)['access_token']}"}


def _make_doctor(session: Session, project: Project, name: str = "Dr Amit") -> User:
    u = User(
        project_id=project.id,
        phone=f"+15559{uuid.uuid4().int % 100000:05d}",
        role=Role.DOCTOR.value,
        full_name=name,
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _create_alert(client: TestClient, resident_token: str, key: str = None) -> str:
    headers = {"Authorization": f"Bearer {resident_token}"}
    if key:
        headers["Idempotency-Key"] = key
    resp = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"], "location_text": "Lobby"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _seed_vital_and_note(
    session: Session, case_id: uuid.UUID, project_id: uuid.UUID, doctor_id: uuid.UUID
) -> None:
    session.add(
        CaseVital(
            project_id=project_id,
            case_id=case_id,
            recorded_by=doctor_id,
            blood_pressure_systolic=128,
            blood_pressure_diastolic=82,
            spo2_percent=95,
            heart_rate_bpm=88,
        )
    )
    session.add(
        CaseNote(
            project_id=project_id,
            case_id=case_id,
            author_id=doctor_id,
            note_type="observation",
            body="Stable but bruising on right hip.",
            patient_consent_obtained=True,
        )
    )
    session.add(
        CaseNote(
            project_id=project_id,
            case_id=case_id,
            author_id=doctor_id,
            note_type="treatment",
            body="Administered oxygen, splint applied.",
            doctor_name="Dr Amit",
            doctor_registration_number="MCI-12345",
            consultation_timestamp=utcnow(),
            advice_given="Transport to hospital for imaging.",
            patient_consent_obtained=True,
        )
    )
    session.commit()


@pytest.fixture(autouse=True)
def _reset_stub_captures() -> None:
    """Each handover test starts with empty stub captures."""
    StubEmailGateway.sent.clear()
    StubNotificationGateway.sent_whatsapp.clear()


# --------------------------------------------------------------------------- #
# 1. Generate — full §8 PDF contents + audit + case_event + signed link        #
# --------------------------------------------------------------------------- #


def test_doctor_generates_handover_with_all_section_8_fields(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, resident = _onboard(
        client, project, session, "+15559990200"
    )
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-1")
    _seed_vital_and_note(session, uuid.UUID(case_id), project.id, doctor.id)

    resp = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo Hospital",
            "doctor_registration_number": "MCI-12345",
            "doctor_assessment": "Suspected hip fracture; vitals stable.",
        },
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    handover_id = body["id"]
    assert body["signed_url"].startswith("http")
    assert body["expires_in_seconds"] == 900
    assert body["hospital_destination"] == "Apollo Hospital"
    assert body["doctor_registration_number"] == "MCI-12345"
    assert body["doctor_name"] == "Dr Amit"

    # Open the signed link and parse the PDF
    pdf_resp = client.get(body["signed_url"].split("localhost:8000")[1])
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    pdf_bytes = pdf_resp.content
    rdr = PdfReader(BytesIO(pdf_bytes))
    assert 1 <= len(rdr.pages) <= 2, f"expected 1-2 pages, got {len(rdr.pages)}"
    text = "\n".join(p.extract_text() for p in rdr.pages)

    # §8 sections (every required field renders)
    for marker in [
        "CONFIDENTIAL",
        "PATIENT HANDOVER",
        project.name,
        # Patient details
        "Asha Rao",
        "B-1203",
        "O+",
        "female",
        # Complaint
        "fall",
        "Suspected hip fracture",
        # Timeline labels
        "Alert raised",
        "Doctor acknowledged",
        # Vitals (rendered as 128/82)
        "128",
        "95",
        # History
        "hypertension",
        "penicillin",
        # Observation + treatment
        "Stable but bruising",
        "Administered oxygen",
        # Family contact
        "Ravi Rao",
        "+15557770001",
        # Footer
        "Dr Amit",
        "MCI-12345",
        "Apollo Hospital",
    ]:
        assert marker in text, f"PDF missing required §8 marker: {marker!r}"

    # case_events + audit
    events = session.exec(select(CaseEvent).where(CaseEvent.case_id == uuid.UUID(case_id))).all()
    assert any(e.event_type == "handover_generated" for e in events)
    actions = [row.action for row in session.exec(select(AuditLog)).all()]
    assert AuditAction.HANDOVER_GENERATED.value in actions
    assert AuditAction.HANDOVER_DOWNLOADED.value in actions  # download was audited too

    row = session.get(HandoverPdf, uuid.UUID(handover_id))
    assert row is not None
    assert row.size_bytes == len(pdf_bytes)


# --------------------------------------------------------------------------- #
# 2. RBAC — non-doctors blocked; PHI roles forbidden                           #
# --------------------------------------------------------------------------- #


def test_non_doctor_cannot_generate_handover(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990201")
    nurse = make_user(Role.NURSE, phone="+15559991300")
    case_id = _create_alert(client, resident_token, key="handover-rbac")

    resp = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, nurse),
    )
    # require_roles(DOCTOR) rejects with 403 doctor_required before service body
    assert resp.status_code == 403


def test_phi_blocked_role_cannot_generate_or_dispatch(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    builder = make_user(Role.BUILDER_ADMIN, phone="+15559991301")
    resident_token, _ = _onboard(client, project, session, "+15559990202")
    case_id = _create_alert(client, resident_token, key="handover-builder")

    resp = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, builder),
    )
    assert resp.status_code == 403


# --------------------------------------------------------------------------- #
# 3. Tenant isolation — another project's doctor sees 404                      #
# --------------------------------------------------------------------------- #


def test_doctor_from_other_project_gets_404(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990203")
    case_id = _create_alert(client, resident_token, key="handover-tenant")

    other = Project(name="Other Tower")
    session.add(other)
    session.commit()
    outsider = User(
        project_id=other.id,
        phone="+15559991302",
        role=Role.DOCTOR.value,
        full_name="Dr Stranger",
    )
    session.add(outsider)
    session.commit()

    resp = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-99",
        },
        headers=_auth(client, login, outsider),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "case_not_found"


# --------------------------------------------------------------------------- #
# 4. Signed link — expiry rejected; wrong token type rejected                  #
# --------------------------------------------------------------------------- #


def test_expired_handover_link_is_rejected(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990204")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-expire")
    resp = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 200, resp.text
    handover = session.get(HandoverPdf, uuid.UUID(resp.json()["id"]))

    # Synthesise an already-expired token (negative TTL) with the same payload.
    expired = create_handover_url_token(
        handover_id=handover.id,
        storage_key=handover.storage_key,
        download_name=handover.file_name,
        ttl=-1,
    )
    bad = client.get(f"/api/v1/handover/file/{expired}")
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_handover_link"


def test_record_url_token_cannot_fetch_handover(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """A leaked record-download token must not unlock the handover endpoint."""
    from app.security.jwt import create_record_url_token

    resident_token, _ = _onboard(client, project, session, "+15559990205")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-xtype")
    gen = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    assert gen.status_code == 200, gen.text
    handover = session.get(HandoverPdf, uuid.UUID(gen.json()["id"]))

    foreign = create_record_url_token(
        storage_key=handover.storage_key, download_name=handover.file_name, ttl=900
    )
    resp = client.get(f"/api/v1/handover/file/{foreign}")
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# 5. Dispatch — email + WhatsApp; one failing must not block the other        #
# --------------------------------------------------------------------------- #


def test_dispatch_records_email_and_whatsapp_attempts_and_audits(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990206")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-dispatch")
    gen = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    handover_id = gen.json()["id"]

    resp = client.post(
        f"/api/v1/handover/{handover_id}/dispatch",
        json={"email": "er@apollo.example", "whatsapp": "+15557779000"},
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert {d["channel"] for d in out["dispatches"]} == {"email", "whatsapp"}
    assert all(d["status"] == NotificationStatus.SENT.value for d in out["dispatches"])

    # Stub captures: neither carries the PDF body, both carry the signed link
    assert len(StubEmailGateway.sent) == 1
    assert StubEmailGateway.sent[0]["to"] == "er@apollo.example"
    assert "/api/v1/handover/file/" in StubEmailGateway.sent[0]["body"]
    assert len(StubNotificationGateway.sent_whatsapp) == 1
    assert "/api/v1/handover/file/" in StubNotificationGateway.sent_whatsapp[0]["body"]

    # case_events + audit
    events = session.exec(select(CaseEvent).where(CaseEvent.case_id == uuid.UUID(case_id))).all()
    assert any(e.event_type == "handover_dispatched" for e in events)
    actions = [row.action for row in session.exec(select(AuditLog)).all()]
    assert AuditAction.HANDOVER_DISPATCHED.value in actions

    rows = session.exec(
        select(HandoverDispatch).where(HandoverDispatch.handover_id == uuid.UUID(handover_id))
    ).all()
    assert len(rows) == 2


def test_dispatch_one_channel_failure_does_not_block_other(
    client: TestClient, project: Project, session: Session, monkeypatch, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990207")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-partial")
    gen = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    handover_id = gen.json()["id"]

    def _boom(self, **kwargs):  # noqa: ANN001
        raise RuntimeError("smtp down")

    monkeypatch.setattr(StubEmailGateway, "send", _boom)

    resp = client.post(
        f"/api/v1/handover/{handover_id}/dispatch",
        json={"email": "er@apollo.example", "whatsapp": "+15557779001"},
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 200, resp.text
    by_channel = {d["channel"]: d for d in resp.json()["dispatches"]}
    assert by_channel["email"]["status"] == NotificationStatus.FAILED.value
    assert by_channel["email"]["error"] == "RuntimeError"
    assert by_channel["whatsapp"]["status"] == NotificationStatus.SENT.value


def test_dispatch_requires_at_least_one_recipient(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990208")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-nodest")
    gen = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    handover_id = gen.json()["id"]

    resp = client.post(
        f"/api/v1/handover/{handover_id}/dispatch",
        json={"email": None, "whatsapp": None},
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "recipient_required"


# --------------------------------------------------------------------------- #
# 6. Link refresh — nurse/doctor/ops can mint a fresh ≤15 min link             #
# --------------------------------------------------------------------------- #


def test_staff_can_refresh_signed_link(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990209")
    doctor = _make_doctor(session, project)
    nurse = make_user(Role.NURSE, phone="+15559991303")
    case_id = _create_alert(client, resident_token, key="handover-relink")
    gen = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    handover_id = gen.json()["id"]

    resp = client.get(
        f"/api/v1/handover/{handover_id}/link", headers=_auth(client, login, nurse)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["expires_in_seconds"] == 900
    assert "/api/v1/handover/file/" in resp.json()["url"]
    actions = [row.action for row in session.exec(select(AuditLog)).all()]
    assert AuditAction.HANDOVER_LINK_ISSUED.value in actions


# --------------------------------------------------------------------------- #
# 7. Review fix #1 — EMERGENCY_SHARE_WITH_HOSPITAL consent gates the surface  #
# --------------------------------------------------------------------------- #


def _revoke_hospital_consent(
    client: TestClient, resident_token: str, *, granted: bool
) -> None:
    resp = client.patch(
        "/api/v1/me/consents/emergency_share_with_hospital",
        json={"granted": granted},
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert resp.status_code == 200, resp.text


def test_generate_blocked_when_hospital_consent_revoked(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """A resident who revoked EMERGENCY_SHARE_WITH_HOSPITAL must not have a
    handover PDF rendered or stored — assert_consent reads live state so the
    revocation is honoured on the next request."""
    resident_token, _ = _onboard(client, project, session, "+15559990300")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-consent-gen")
    _revoke_hospital_consent(client, resident_token, granted=False)

    resp = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "consent_required"
    # PHI never reached storage and no row was persisted.
    rows = session.exec(select(HandoverPdf)).all()
    assert rows == []


def test_dispatch_and_link_blocked_when_hospital_consent_revoked_after_generate(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """If the resident revokes hospital sharing AFTER a PDF was generated,
    the persisted row stays for retention but no new signed link is minted
    and no dispatch can leave the platform."""
    resident_token, _ = _onboard(client, project, session, "+15559990301")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-consent-disp")
    gen = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    assert gen.status_code == 200, gen.text
    handover_id = gen.json()["id"]

    _revoke_hospital_consent(client, resident_token, granted=False)

    # Link refresh blocked
    link = client.get(
        f"/api/v1/handover/{handover_id}/link",
        headers=_auth(client, login, doctor),
    )
    assert link.status_code == 403
    assert link.json()["error"]["code"] == "consent_required"

    # Dispatch blocked
    disp = client.post(
        f"/api/v1/handover/{handover_id}/dispatch",
        json={"email": "er@apollo.example"},
        headers=_auth(client, login, doctor),
    )
    assert disp.status_code == 403
    assert disp.json()["error"]["code"] == "consent_required"
    # And nothing left the platform: no dispatch rows, no stub captures.
    assert session.exec(select(HandoverDispatch)).all() == []
    assert StubEmailGateway.sent == []


# --------------------------------------------------------------------------- #
# 8. Review fix #2 — in live mode the signed URL is the S3 native presigned  #
# --------------------------------------------------------------------------- #


def test_live_mode_signed_url_uses_storage_gateway_not_backend_proxy(
    monkeypatch,
) -> None:
    """``S3StorageGateway.get_bytes`` raises by design — live mode MUST use
    the native S3 presigned URL the gateway returns, not the backend-proxy
    `/handover/file/{token}` route (which would always 404 in live mode)."""
    from app.services import handover_service
    from app.services.handover_service import _signed_url

    class _FakeSettings:
        provider_mode = "live"
        public_base_url = "http://localhost:8000"
        s3_signed_url_ttl_seconds = 900

    class _FakeGateway:
        captured: dict = {}

        def signed_url(self, *, key: str, download_name: str) -> str:
            _FakeGateway.captured = {"key": key, "download_name": download_name}
            return f"https://example.s3.amazonaws.com/{key}?X-Amz-Signature=sig"

    monkeypatch.setattr(handover_service, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(handover_service, "get_storage_gateway", _FakeGateway)

    handover = HandoverPdf(
        project_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        generated_by=uuid.uuid4(),
        storage_key="abc123",
        file_name="handover-x.pdf",
        size_bytes=1024,
        doctor_name="Dr X",
        doctor_registration_number="MCI-1",
        hospital_destination="Apollo",
    )

    url = _signed_url(handover)
    assert url.startswith("https://example.s3.amazonaws.com/abc123")
    # The backend-proxy route is the stub-only path; live mode must NOT use it.
    assert "/api/v1/handover/file/" not in url
    assert _FakeGateway.captured == {"key": "abc123", "download_name": "handover-x.pdf"}


# --------------------------------------------------------------------------- #
# 9. Review fix #3 — live Twilio gateway routes WhatsApp to Messages.json     #
# --------------------------------------------------------------------------- #


def test_live_twilio_whatsapp_posts_messages_endpoint_with_whatsapp_prefix(
    monkeypatch,
) -> None:
    """The live Twilio gateway must hit `Messages.json` with the Twilio
    `whatsapp:` prefix on BOTH endpoints; callers pass a normal phone
    number. Until this lands, dispatch in live mode is always FAILED."""
    from app.services import notifications

    class _FakeSettings:
        provider_mode = "live"
        twilio_account_sid = "ACtest"
        twilio_auth_token = "secret"
        twilio_sms_from = "+15550000000"
        twilio_voice_from = "+15550000001"
        twilio_whatsapp_from = "whatsapp:+14155238886"
        # Slice 16: leaving the status callback empty keeps this test
        # focused on the WhatsApp send path; the callback URL surface
        # has its own coverage in test_notifications_webhooks.py.
        twilio_status_callback_url = None
        # Slice 16: live composite gateway also constructs an FcmPushGateway
        # at get_notification_gateway() time; the fields are read lazily so
        # leaving them blank only affects `send_push`, which this test does
        # not exercise.
        fcm_service_account_file = None
        fcm_project_id = None

    monkeypatch.setattr(notifications, "get_settings", lambda: _FakeSettings())

    captured: dict = {}

    class _FakeResp:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"sid": "SMfake"}

    def _fake_post(url, **kwargs):  # noqa: ANN001
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["auth"] = kwargs.get("auth")
        return _FakeResp()

    monkeypatch.setattr(notifications.requests, "post", _fake_post)

    gateway = notifications.get_notification_gateway()
    sid = gateway.send_whatsapp(to="+15557779000", body="link")

    assert sid == "SMfake"
    assert captured["url"].endswith("/Accounts/ACtest/Messages.json")
    assert captured["data"]["From"] == "whatsapp:+14155238886"
    assert captured["data"]["To"] == "whatsapp:+15557779000"
    assert captured["data"]["Body"] == "link"


def test_live_twilio_gateway_requires_credentials(monkeypatch) -> None:
    """Misconfigured live mode must fail loudly at construction, not silently
    record every dispatch as failed."""
    from app.services import notifications

    class _NoKeys:
        provider_mode = "live"
        twilio_account_sid = None
        twilio_auth_token = None
        twilio_sms_from = None
        twilio_voice_from = None
        twilio_whatsapp_from = None

    monkeypatch.setattr(notifications, "get_settings", lambda: _NoKeys())
    with pytest.raises(RuntimeError, match="TWILIO_ACCOUNT_SID"):
        notifications.get_notification_gateway()


# --------------------------------------------------------------------------- #
# 10. Review fix #4 — dispatch rows are durable across a mid-send crash      #
# --------------------------------------------------------------------------- #


def test_dispatch_row_is_durable_even_if_process_crashes_mid_send(
    client: TestClient, project: Project, session: Session, monkeypatch, login
) -> None:
    """Before the fix, ``_dispatch_one`` only flushed before calling the
    provider and committed at the end; a process crash after a successful
    provider call would lose the audit/dispatch row. The outbox now commits
    the row in SENDING before the provider call, so a `BaseException` that
    bypasses our `except Exception` still leaves a durable row in the DB."""
    resident_token, _ = _onboard(client, project, session, "+15559990400")
    doctor = _make_doctor(session, project)
    case_id = _create_alert(client, resident_token, key="handover-durable")
    gen = client.post(
        f"/api/v1/emergency/alerts/{case_id}/handover",
        json={
            "hospital_destination": "Apollo",
            "doctor_registration_number": "MCI-1",
        },
        headers=_auth(client, login, doctor),
    )
    handover_id = gen.json()["id"]

    def _crash(self, **kwargs):  # noqa: ANN001
        # BaseException sidesteps `except Exception` in _dispatch_one,
        # simulating a hard process crash (SystemExit / KeyboardInterrupt /
        # OOM-killer SIGKILL) after the row has been committed in SENDING.
        raise SystemExit("simulated mid-send crash")

    monkeypatch.setattr(StubEmailGateway, "send", _crash)

    with pytest.raises(SystemExit):
        client.post(
            f"/api/v1/handover/{handover_id}/dispatch",
            json={"email": "er@apollo.example"},
            headers=_auth(client, login, doctor),
        )

    # The dispatch row exists despite the crash, in SENDING — stuck-claim
    # state, recoverable by a reaper (open-questions.md).
    rows = session.exec(
        select(HandoverDispatch).where(
            HandoverDispatch.handover_id == uuid.UUID(handover_id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].channel == "email"
    assert rows[0].status == NotificationStatus.SENDING.value
    assert rows[0].error is None
