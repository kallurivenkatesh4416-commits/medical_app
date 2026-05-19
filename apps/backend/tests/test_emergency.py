"""Slice 5 — emergency happy path: idempotent alert + push + feed."""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.enums import AuditAction, CaseStatus, NotificationChannel, NotificationStatus, Role
from app.models.audit import AuditLog
from app.models.emergency import CaseEvent, EmergencyCase, NotificationAttempt
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User


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


def _register_doctor_push(client: TestClient, headers: dict[str, str]) -> None:
    resp = client.post(
        "/api/v1/devices/push-token",
        json={"token": "doctor-device-token", "platform": "web"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


def test_resident_alert_creates_case_push_attempt_feed_and_audit(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, resident = _onboard(client, project, session, "+15559990001")
    doctor = make_user(Role.DOCTOR, phone="+15559991111")
    doctor_headers = _auth(client, login, doctor)
    _register_doctor_push(client, doctor_headers)

    resp = client.post(
        "/api/v1/emergency/alerts",
        json={
            "symptom_codes": ["chest_pain"],
            "location_text": "Clubhouse lobby",
            "latitude": 17.4,
            "longitude": 78.4,
        },
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "case-1",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resident_id"] == str(resident.id)
    assert body["status"] == CaseStatus.ALERTED.value
    assert body["assigned_doctor_id"] == str(doctor.id)
    assert body["notification_attempts"] == [
        {
            "channel": NotificationChannel.FCM.value,
            "recipient_id": str(doctor.id),
            "status": NotificationStatus.SENT.value,
            "provider_ref": "stub-push",
            "error": None,
        }
    ]

    feed = client.get("/api/v1/emergency/alerts/active", headers=doctor_headers)
    assert feed.status_code == 200, feed.text
    assert feed.json()[0]["id"] == body["id"]
    assert feed.json()[0]["resident_name"] == "Asha Rao"

    cases = session.exec(select(EmergencyCase)).all()
    assert len(cases) == 1
    assert session.exec(select(NotificationAttempt)).first().status == "sent"
    assert session.exec(select(CaseEvent)).first().event_type == "alert_created"
    actions = list(session.exec(select(AuditLog.action)).all())
    assert AuditAction.EMERGENCY_ALERT_CREATED.value in actions
    assert AuditAction.EMERGENCY_ALERT_LIST.value in actions


def test_alert_requires_idempotency_and_exact_replay(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990002")
    doctor = make_user(Role.DOCTOR, phone="+15559991112")
    _register_doctor_push(client, _auth(client, login, doctor))
    headers = {"Authorization": f"Bearer {resident_token}", "Idempotency-Key": "same"}

    missing = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "idempotency_key_required"

    first = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers=headers,
    )
    assert first.status_code == 200
    replay = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == first.json()["id"]
    assert len(session.exec(select(EmergencyCase)).all()) == 1

    changed = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["breathlessness"]},
        headers=headers,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_key_conflict"


def test_feed_is_role_and_tenant_isolated(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990003")
    doctor = make_user(Role.DOCTOR, phone="+15559991113")
    doctor_headers = _auth(client, login, doctor)
    _register_doctor_push(client, doctor_headers)
    assert (
        client.post(
            "/api/v1/emergency/alerts",
            json={"symptom_codes": ["fall"]},
            headers={
                "Authorization": f"Bearer {resident_token}",
                "Idempotency-Key": "case-tenant",
            },
        ).status_code
        == 200
    )

    for role in (Role.BUILDER_ADMIN, Role.SECURITY_DESK):
        blocked = make_user(role)
        resp = client.get(
            "/api/v1/emergency/alerts/active",
            headers=_auth(client, login, blocked),
        )
        assert resp.status_code == 403

    other_project = Project(name="Other Residency")
    session.add(other_project)
    session.commit()
    outsider = User(
        project_id=other_project.id,
        phone="+15559991114",
        role=Role.DOCTOR.value,
        full_name="Outside Doctor",
    )
    session.add(outsider)
    session.commit()
    cross = client.get(
        "/api/v1/emergency/alerts/active",
        headers=_auth(client, login, outsider),
    )
    assert cross.status_code == 200
    assert cross.json() == []


def test_alert_still_creates_case_when_doctor_has_no_push_token(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990004")
    doctor = make_user(Role.DOCTOR, phone="+15559991115")
    resp = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "no-token",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_doctor_id"] == str(doctor.id)
    attempt = session.exec(select(NotificationAttempt)).first()
    assert attempt.status == NotificationStatus.FAILED.value
    assert attempt.error == "no_push_token"
