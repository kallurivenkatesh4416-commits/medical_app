"""Slice 3 — resident onboarding, consent persistence, PHI-guarded profile,
live consent enforcement, and data_storage account closure."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.enums import AuditAction, ConsentType, Role
from app.models.audit import AuditLog
from app.models.project import Project


def _register_token(client: TestClient, phone: str) -> str:
    client.post("/api/v1/auth/otp/request", json={"phone": phone})
    req = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    code = req.json()["dev_otp"]
    vr = client.post("/api/v1/auth/otp/verify", json={"phone": phone, "code": code})
    assert vr.status_code == 200, vr.text
    body = vr.json()
    assert body["registration_required"] is True
    assert body["access_token"] is None
    return body["registration_token"]


def _payload(project_id: str, **overrides) -> dict:
    data = {
        "full_name": "Asha Rao",
        "dob": "1948-03-02",
        "gender": "female",
        "project_id": project_id,
        "flat_villa_number": "B-1203",
        "emergency_contacts": [
            {"name": "Ravi Rao", "phone": "+15557770001", "relation": "son",
             "is_primary": True}
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
    data.update(overrides)
    return data


def _onboard(client: TestClient, project: Project, phone: str, **overrides):
    tok = _register_token(client, phone)
    return client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id), **overrides),
        headers={"Authorization": f"Bearer {tok}"},
    )


def test_new_phone_offered_registration(client: TestClient) -> None:
    tok = _register_token(client, "+15556660001")
    assert tok


def test_projects_lookup_requires_registration_token(
    client: TestClient, project: Project
) -> None:
    assert client.get("/api/v1/projects").status_code == 401
    tok = _register_token(client, "+15556660002")
    resp = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {tok}"})
    assert resp.status_code == 200
    assert any(p["id"] == str(project.id) for p in resp.json())


def test_onboarding_creates_resident_and_consents(
    client: TestClient, project: Project, session: Session
) -> None:
    resp = _onboard(client, project, "+15556660003")
    assert resp.status_code == 200, resp.text
    access = resp.json()["access_token"]

    prof = client.get(
        "/api/v1/me/profile", headers={"Authorization": f"Bearer {access}"}
    )
    assert prof.status_code == 200
    body = prof.json()
    assert body["full_name"] == "Asha Rao"
    assert body["blood_group"] == "O+"
    assert {c["consent_type"]: c["granted"] for c in body["consents"]}[
        ConsentType.FAMILY_MEMBER_ACCESS.value
    ] is False

    actions = set(session.exec(select(AuditLog.action)).all())
    assert AuditAction.RESIDENT_REGISTERED.value in actions
    assert AuditAction.DISCLAIMER_ACKNOWLEDGED.value in actions
    assert AuditAction.CONSENT_GRANTED.value in actions
    assert AuditAction.CONSENT_REVOKED.value in actions  # the declined one


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"disclaimer_acknowledged": False}, "disclaimer_required"),
        (
            {"consents": [{"consent_type": "data_storage", "granted": False},
                          {"consent_type": "emergency_share_with_doctor", "granted": True},
                          {"consent_type": "emergency_share_with_hospital", "granted": True},
                          {"consent_type": "family_member_access", "granted": True},
                          {"consent_type": "medicine_reminder_notifications", "granted": True}]},
            "data_storage_required",
        ),
        (
            {"consents": [{"consent_type": "data_storage", "granted": True}]},
            "consent_incomplete",
        ),
    ],
)
def test_onboarding_validation(
    client: TestClient, project: Project, overrides, code
) -> None:
    resp = _onboard(client, project, "+15556660004", **overrides)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == code


def test_onboarding_rejects_duplicate_and_unknown_project(
    client: TestClient, project: Project
) -> None:
    # One registration token, used twice: the second attempt must 409 because
    # the account now exists (the token itself is still valid).
    tok = _register_token(client, "+15556660005")
    hdr = {"Authorization": f"Bearer {tok}"}
    first = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers=hdr,
    )
    assert first.status_code == 200, first.text
    dup = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers=hdr,
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "already_registered"

    tok = _register_token(client, "+15556660006")
    bad = client.post(
        "/api/v1/onboarding/complete",
        json=_payload("00000000-0000-0000-0000-000000000000"),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert bad.status_code == 404


def test_consent_revocation_takes_effect_immediately(
    client: TestClient, project: Project, make_user, login
) -> None:
    onboard = _onboard(client, project, "+15556660007")
    resident_access = onboard.json()["access_token"]
    resident_id = client.get(
        "/api/v1/me/profile",
        headers={"Authorization": f"Bearer {resident_access}"},
    ).json()["resident_id"]

    doctor = make_user(Role.DOCTOR, phone="+15556661111")
    d_tok = login(doctor.phone)["access_token"]
    d_hdr = {"Authorization": f"Bearer {d_tok}"}

    # Consent granted at onboarding -> doctor can view.
    ok = client.get(f"/api/v1/residents/{resident_id}/profile", headers=d_hdr)
    assert ok.status_code == 200

    # Resident revokes it; the very next staff read is blocked.
    rv = client.patch(
        "/api/v1/me/consents/emergency_share_with_doctor",
        json={"granted": False},
        headers={"Authorization": f"Bearer {resident_access}"},
    )
    assert rv.status_code == 200
    blocked = client.get(f"/api/v1/residents/{resident_id}/profile", headers=d_hdr)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "consent_required"


@pytest.mark.parametrize("role", [Role.BUILDER_ADMIN, Role.SECURITY_DESK])
def test_phi_roles_cannot_view_resident_profile(
    client: TestClient, project: Project, make_user, login, role
) -> None:
    onboard = _onboard(client, project, "+15556660008")
    resident_id = client.get(
        "/api/v1/me/profile",
        headers={"Authorization": f"Bearer {onboard.json()['access_token']}"},
    ).json()["resident_id"]

    u = make_user(role)
    tok = login(u.phone)["access_token"]
    resp = client.get(
        f"/api/v1/residents/{resident_id}/profile",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 403


def test_revoking_data_storage_closes_account(
    client: TestClient, project: Project
) -> None:
    onboard = _onboard(client, project, "+15556660009")
    access = onboard.json()["access_token"]
    refresh = onboard.json()["refresh_token"]
    hdr = {"Authorization": f"Bearer {access}"}

    resp = client.patch(
        "/api/v1/me/consents/data_storage", json={"granted": False}, headers=hdr
    )
    assert resp.status_code == 200
    assert resp.json()["account_closed"] is True

    # Account is now soft-deleted: access + refresh are dead.
    assert client.get("/api/v1/me/profile", headers=hdr).status_code == 401
    assert (
        client.post("/api/v1/auth/refresh", json={"refresh_token": refresh}).status_code
        == 401
    )
