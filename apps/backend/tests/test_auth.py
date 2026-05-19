"""Slice 2 — OTP login, JWT issuance, refresh rotation, logout."""

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.enums import Role
from app.models.auth import OtpCode
from app.models.base import utcnow


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_otp_login_happy_path(client: TestClient, make_user, login) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110001")
    tokens = login(user.phone)
    assert tokens["token_type"] == "bearer"

    me = client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["phone"] == user.phone
    assert body["role"] == Role.DOCTOR.value


def test_otp_request_does_not_leak_code_in_non_local(
    client: TestClient, make_user, monkeypatch
) -> None:
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "prod")
    make_user(Role.RESIDENT, phone="+15551110002")
    resp = client.post("/api/v1/auth/otp/request", json={"phone": "+15551110002"})
    assert resp.status_code == 200
    assert resp.json()["dev_otp"] is None
    get_settings.cache_clear()


def test_wrong_code_rejected_with_envelope(client: TestClient, make_user) -> None:
    make_user(Role.NURSE, phone="+15551110003")
    client.post("/api/v1/auth/otp/request", json={"phone": "+15551110003"})
    resp = client.post(
        "/api/v1/auth/otp/verify", json={"phone": "+15551110003", "code": "000000"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_otp"


def test_expired_code_rejected(
    client: TestClient, make_user, session: Session
) -> None:
    make_user(Role.OPS, phone="+15551110004")
    req = client.post("/api/v1/auth/otp/request", json={"phone": "+15551110004"})
    code = req.json()["dev_otp"]
    otp = session.exec(
        select(OtpCode).where(OtpCode.phone == "+15551110004")
    ).first()
    otp.expires_at = utcnow() - timedelta(seconds=1)
    session.add(otp)
    session.commit()
    resp = client.post(
        "/api/v1/auth/otp/verify", json={"phone": "+15551110004", "code": code}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_otp"


def test_verified_phone_without_account_cannot_login(client: TestClient) -> None:
    client.post("/api/v1/auth/otp/request", json={"phone": "+15559990000"})
    req = client.post("/api/v1/auth/otp/request", json={"phone": "+15559990000"})
    code = req.json()["dev_otp"]
    resp = client.post(
        "/api/v1/auth/otp/verify", json={"phone": "+15559990000", "code": code}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "registration_required"


def test_refresh_rotation_and_reuse_detection(
    client: TestClient, make_user, login
) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110005")
    tokens = login(user.phone)
    first_refresh = tokens["refresh_token"]

    r1 = client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert r1.status_code == 200
    new_refresh = r1.json()["refresh_token"]
    assert new_refresh != first_refresh

    # Reusing the old (now rotated) refresh token is blocked...
    reuse = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "refresh_reuse_detected"

    # ...and the whole family is revoked, so the new one is dead too.
    after = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_refresh}
    )
    assert after.status_code == 401


def test_logout_revokes_refresh(client: TestClient, make_user, login) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110006")
    tokens = login(user.phone)
    out = client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert out.status_code == 204
    resp = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert resp.status_code == 401
