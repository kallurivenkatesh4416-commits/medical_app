"""Slice 4 — medical record upload, signed links, RBAC/consent, audit."""

import uuid
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import get_settings
from app.enums import AuditAction, ConsentType, Role
from app.models.audit import AuditLog
from app.models.medical_record import MedicalRecord
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User
from app.security.jwt import create_record_url_token


@pytest.fixture(autouse=True)
def _storage_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_STORAGE_DIR", str(tmp_path / "records"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
    # Deliberately above the legal/brief cap; link responses must still be 900s.
    monkeypatch.setenv("S3_SIGNED_URL_TTL_SECONDS", "9999")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


def _upload(
    client: TestClient,
    access: str,
    *,
    key: str = "record-key",
    body: bytes = b"%PDF-1.4 test record",
    filename: str = "lab.pdf",
    content_type: str = "application/pdf",
    record_type: str = "lab",
):
    return client.post(
        "/api/v1/me/records",
        headers={"Authorization": f"Bearer {access}", "Idempotency-Key": key},
        data={
            "record_type": record_type,
            "record_date": "2026-05-19",
            "source": "Apollo Clinic",
            "tags": '["lab","blood"]',
        },
        files={"file": (filename, body, content_type)},
    )


def _auth(client: TestClient, login, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(user.phone)['access_token']}"}


def test_resident_upload_list_link_download_and_audit(
    client: TestClient, project: Project, session: Session
) -> None:
    access, _resident = _onboard(client, project, session, "+15558880001")

    uploaded = _upload(client, access)
    assert uploaded.status_code == 200, uploaded.text
    rec = uploaded.json()
    assert rec["file_name"] == "lab.pdf"
    assert rec["record_type"] == "lab"
    assert rec["tags"] == ["lab", "blood"]

    listed = client.get("/api/v1/me/records", headers={"Authorization": f"Bearer {access}"})
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json()] == [rec["id"]]

    link = client.get(
        f"/api/v1/me/records/{rec['id']}/link",
        headers={"Authorization": f"Bearer {access}"},
    )
    assert link.status_code == 200, link.text
    assert link.json()["expires_in_seconds"] == 900

    downloaded = client.get(link.json()["url"])
    assert downloaded.status_code == 200
    assert downloaded.content == b"%PDF-1.4 test record"
    assert downloaded.headers["content-type"] == "application/pdf"
    assert 'filename="lab.pdf"' in downloaded.headers["content-disposition"]

    actions = list(session.exec(select(AuditLog.action)).all())
    assert AuditAction.RECORD_UPLOADED.value in actions
    assert AuditAction.RECORD_LIST.value in actions
    assert AuditAction.RECORD_LINK_ISSUED.value in actions


def test_upload_idempotency_replay_conflict_and_owner_binding(
    client: TestClient, project: Project, session: Session
) -> None:
    access_a, _ = _onboard(client, project, session, "+15558880002")
    first = _upload(client, access_a, key="shared")
    assert first.status_code == 200, first.text

    retry = _upload(client, access_a, key="shared")
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert len(session.exec(select(MedicalRecord)).all()) == 1

    changed = _upload(client, access_a, key="shared", body=b"%PDF changed")
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_key_conflict"
    assert len(session.exec(select(MedicalRecord)).all()) == 1

    access_b, _ = _onboard(client, project, session, "+15558880003")
    stolen = _upload(client, access_b, key="shared")
    assert stolen.status_code == 409
    assert stolen.json()["error"]["code"] == "idempotency_key_conflict"


def test_signed_download_rejects_tampered_and_expired_tokens(
    client: TestClient, project: Project, session: Session
) -> None:
    access, _ = _onboard(client, project, session, "+15558880004")
    uploaded = _upload(client, access)
    assert uploaded.status_code == 200
    rec = session.get(MedicalRecord, uuid.UUID(uploaded.json()["id"]))

    link = client.get(
        f"/api/v1/me/records/{rec.id}/link",
        headers={"Authorization": f"Bearer {access}"},
    )
    token = urlparse(link.json()["url"]).path.rsplit("/", 1)[-1]
    tampered = client.get(f"/api/v1/records/download/{token}x")
    assert tampered.status_code == 401
    assert tampered.json()["error"]["code"] == "invalid_record_link"

    expired = create_record_url_token(
        storage_key=rec.storage_key, download_name=rec.file_name, ttl=-1
    )
    resp = client.get(f"/api/v1/records/download/{expired}")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_record_link"


def test_upload_validation_blocks_bad_type_and_oversize(
    client: TestClient, project: Project, session: Session, monkeypatch
) -> None:
    access, _ = _onboard(client, project, session, "+15558880005")

    bad_type = _upload(client, access, content_type="text/plain")
    assert bad_type.status_code == 415
    assert bad_type.json()["error"]["code"] == "unsupported_type"

    monkeypatch.setenv("MAX_UPLOAD_BYTES", "4")
    get_settings.cache_clear()
    too_large = _upload(client, access, key="large")
    assert too_large.status_code == 413
    assert too_large.json()["error"]["code"] == "file_too_large"


def test_upload_rejects_declared_type_mismatch_before_storage(
    client: TestClient, project: Project, session: Session
) -> None:
    access, _ = _onboard(client, project, session, "+15558880015")
    png = b"\x89PNG\r\n\x1a\n" + b"slice-18"

    mismatch = _upload(
        client,
        access,
        body=png,
        content_type="application/pdf",
        filename="mask.pdf",
    )

    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "file_type_mismatch"
    assert session.exec(select(MedicalRecord)).all() == []
    rejected = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.RECORD_UPLOAD_REJECTED.value
        )
    ).one()
    assert rejected.meta == {
        "declared_type": "application/pdf",
        "sniffed_type": "image/png",
        "size_bytes": len(png),
        "reason": "mime_mismatch",
    }


def test_upload_stores_detected_type_and_rejects_unknown_bytes(
    client: TestClient, project: Project, session: Session
) -> None:
    access, _ = _onboard(client, project, session, "+15558880016")
    jpeg = b"\xff\xd8\xff\xe0" + b"slice-18-jpeg"
    mismatch = _upload(
        client,
        access,
        key="jpeg-mask",
        body=jpeg,
        filename="scan.png",
        content_type="image/png",
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "file_type_mismatch"

    unknown = _upload(
        client,
        access,
        key="unknown",
        body=b"tiny",
        content_type="application/pdf",
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "file_type_mismatch"

    good = _upload(client, access, key="good-detected")
    assert good.status_code == 200, good.text
    rec = session.get(MedicalRecord, uuid.UUID(good.json()["id"]))
    assert rec.content_type == "application/pdf"


def test_upload_stub_virus_scan_quarantines_eicar(
    client: TestClient, project: Project, session: Session
) -> None:
    access, _ = _onboard(client, project, session, "+15558880017")
    eicar = (
        b"%PDF-1.4\n"
        b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
        b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )

    quarantined = _upload(client, access, body=eicar, key="eicar")

    assert quarantined.status_code == 422
    assert quarantined.json()["error"]["code"] == "record_quarantined"
    assert session.exec(select(MedicalRecord)).all() == []
    rejected = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.RECORD_UPLOAD_REJECTED.value
        )
    ).one()
    assert rejected.meta["reason"] == "virus_signature"
    assert rejected.meta["signature"] == "Eicar-Test-Signature"
    assert rejected.meta["scanner"] == "stub"


def test_upload_fails_closed_when_clamav_is_not_configured(
    client: TestClient,
    project: Project,
    session: Session,
    monkeypatch,
) -> None:
    access, _ = _onboard(client, project, session, "+15558880018")
    monkeypatch.setenv("VIRUS_SCAN_MODE", "clamav")
    monkeypatch.delenv("CLAMAV_HOST", raising=False)
    get_settings.cache_clear()

    unavailable = _upload(client, access, key="clamav")

    assert unavailable.status_code == 502
    assert unavailable.json()["error"]["code"] == "record_scan_unavailable"
    assert session.exec(select(MedicalRecord)).all() == []
    get_settings.cache_clear()


def test_staff_records_are_consent_gated_phi_blocked_and_tenant_isolated(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    access, resident = _onboard(client, project, session, "+15558880006")
    uploaded = _upload(client, access)
    assert uploaded.status_code == 200
    record_id = uploaded.json()["id"]

    doctor = make_user(Role.DOCTOR, phone="+15558881111")
    doctor_headers = _auth(client, login, doctor)
    staff_list = client.get(
        f"/api/v1/residents/{resident.id}/records", headers=doctor_headers
    )
    assert staff_list.status_code == 200
    assert staff_list.json()[0]["id"] == record_id

    staff_link = client.get(
        f"/api/v1/residents/{resident.id}/records/{record_id}/link",
        headers=doctor_headers,
    )
    assert staff_link.status_code == 200
    assert staff_link.json()["expires_in_seconds"] == 900

    for role in (Role.BUILDER_ADMIN, Role.SECURITY_DESK):
        blocked = make_user(role)
        resp = client.get(
            f"/api/v1/residents/{resident.id}/records",
            headers=_auth(client, login, blocked),
        )
        assert resp.status_code == 403

    other_project = Project(name="Other Residency")
    session.add(other_project)
    session.commit()
    outsider = User(
        project_id=other_project.id,
        phone="+15558881112",
        role=Role.DOCTOR.value,
        full_name="Outside Doctor",
    )
    session.add(outsider)
    session.commit()
    cross = client.get(
        f"/api/v1/residents/{resident.id}/records",
        headers=_auth(client, login, outsider),
    )
    assert cross.status_code == 404

    revoke = client.patch(
        f"/api/v1/me/consents/{ConsentType.EMERGENCY_SHARE_WITH_DOCTOR.value}",
        json={"granted": False},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert revoke.status_code == 200
    denied = client.get(
        f"/api/v1/residents/{resident.id}/records", headers=doctor_headers
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "consent_required"
