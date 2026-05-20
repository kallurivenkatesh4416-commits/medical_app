"""Slice 3 — resident onboarding, consent persistence, PHI-guarded profile,
live consent enforcement, and data_storage account closure."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.enums import AuditAction, ConsentType, Role
from app.models.audit import AuditLog
from app.models.auth import RefreshToken
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User


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

    actions = list(session.exec(select(AuditLog.action)).all())
    actions_set = set(actions)
    assert AuditAction.RESIDENT_REGISTERED.value in actions_set
    assert AuditAction.DISCLAIMER_ACKNOWLEDGED.value in actions_set
    assert AuditAction.CONSENT_GRANTED.value in actions_set
    assert AuditAction.CONSENT_REVOKED.value in actions_set  # the declined one
    # Slice 11 contract: the disclaimer ack screen is the single bottleneck
    # before consents/profile are persisted — the audit row must land
    # exactly once per successful onboarding, never zero (a buried checkbox
    # bypass) and never twice (the onboarding-idempotency replay path
    # short-circuits before re-auditing).
    assert actions.count(AuditAction.DISCLAIMER_ACKNOWLEDGED.value) == 1


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
    client: TestClient, project: Project, session: Session
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

    # Closure is atomic with its audit trail: both rows are present.
    actions = list(session.exec(select(AuditLog.action)).all())
    assert actions.count(AuditAction.ACCOUNT_CLOSURE_INITIATED.value) == 1
    assert AuditAction.CONSENT_REVOKED.value in actions


def test_onboarding_is_idempotent_with_key(
    client: TestClient, project: Project, session: Session
) -> None:
    tok = _register_token(client, "+15556660010")
    headers = {
        "Authorization": f"Bearer {tok}",
        "Idempotency-Key": "onboard-key-abc",
    }
    payload = _payload(str(project.id))

    first = client.post(
        "/api/v1/onboarding/complete", json=payload, headers=headers
    )
    assert first.status_code == 200, first.text

    # Same key (lost-response retry) -> 200 with a working session, not 409.
    retry = client.post(
        "/api/v1/onboarding/complete", json=payload, headers=headers
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["access_token"]

    # Exactly one account + one resident created.
    users = session.exec(
        select(User).where(User.phone == "+15556660010")
    ).all()
    assert len(users) == 1
    residents = session.exec(
        select(Resident).where(Resident.user_id == users[0].id)
    ).all()
    assert len(residents) == 1
    # Slice 11 contract: the lost-response retry must NOT re-audit the
    # disclaimer ack — exactly one row across both calls, matching the
    # "exactly one per successful onboarding" invariant from
    # `test_onboarding_creates_resident_and_consents`.
    actions = list(session.exec(select(AuditLog.action)).all())
    assert actions.count(AuditAction.DISCLAIMER_ACKNOWLEDGED.value) == 1


def test_idempotency_key_is_owner_bound(
    client: TestClient, project: Project, session: Session
) -> None:
    # Phone A creates an account with a key.
    tok_a = _register_token(client, "+15556660011")
    first = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers={"Authorization": f"Bearer {tok_a}", "Idempotency-Key": "shared"},
    )
    assert first.status_code == 200

    # Phone B reuses A's key -> must be a conflict, NOT A's tokens.
    tok_b = _register_token(client, "+15556660012")
    stolen = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers={"Authorization": f"Bearer {tok_b}", "Idempotency-Key": "shared"},
    )
    assert stolen.status_code == 409
    assert stolen.json()["error"]["code"] == "idempotency_key_conflict"
    assert "access_token" not in stolen.json()
    # B never got an account.
    assert (
        session.exec(select(User).where(User.phone == "+15556660012")).first()
        is None
    )


def test_new_key_after_success_is_not_replayed(
    client: TestClient, project: Project
) -> None:
    tok = _register_token(client, "+15556660014")

    ok = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": "first-key"},
    )
    assert ok.status_code == 200

    # Same (still valid) registration token, a DIFFERENT key + changed body.
    # The phone is registered but this key never created it -> must 409, must
    # NOT mint fresh tokens.
    reused = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id), full_name="Sneaky Rename"),
        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": "new-key"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "already_registered"
    assert "access_token" not in reused.json()

    # A new key with the SAME body is still a genuine duplicate, not a replay.
    again = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": "another"},
    )
    assert again.status_code == 409
    assert "access_token" not in again.json()


def test_replay_after_account_closure_is_refused(
    client: TestClient, project: Project, session: Session
) -> None:
    tok = _register_token(client, "+15556660015")
    headers = {"Authorization": f"Bearer {tok}", "Idempotency-Key": "ck"}
    payload = _payload(str(project.id))

    created = client.post(
        "/api/v1/onboarding/complete", json=payload, headers=headers
    )
    assert created.status_code == 200
    access = created.json()["access_token"]

    closed = client.patch(
        "/api/v1/me/consents/data_storage",
        json={"granted": False},
        headers={"Authorization": f"Bearer {access}"},
    )
    assert closed.status_code == 200
    assert closed.json()["account_closed"] is True

    # Exact replay (same token + key + body) must NOT resurrect the closed
    # account or mint a fresh refresh token.
    replay = client.post(
        "/api/v1/onboarding/complete", json=payload, headers=headers
    )
    assert replay.status_code == 403
    assert replay.json()["error"]["code"] == "account_inactive"
    assert "access_token" not in replay.json()

    user = session.exec(
        select(User).where(User.phone == "+15556660015")
    ).first()
    assert user.deleted_at is not None
    live = session.exec(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    assert live == []


def test_idempotency_same_key_different_body_conflicts(
    client: TestClient, project: Project
) -> None:
    tok = _register_token(client, "+15556660013")
    hdr = {"Authorization": f"Bearer {tok}", "Idempotency-Key": "k2"}
    assert (
        client.post(
            "/api/v1/onboarding/complete",
            json=_payload(str(project.id)),
            headers=hdr,
        ).status_code
        == 200
    )
    changed = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id), full_name="Different Name"),
        headers=hdr,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_key_conflict"
