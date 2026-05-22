"""Slice 2 — OTP login, JWT issuance, refresh rotation, logout."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import Settings, get_settings
from app.enums import AuditAction, Role
from app.main import create_app
from app.models.audit import AuditLog
from app.models.auth import OtpCode, RefreshToken
from app.models.base import utcnow
from app.models.user import User
from app.security.dashboard_session import (
    DASHBOARD_ACCESS_COOKIE,
    DASHBOARD_CSRF_HEADER,
    DASHBOARD_REFRESH_COOKIE,
    DASHBOARD_SESSION_COOKIE,
)
from app.services import auth_service


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _dashboard_login(client: TestClient, phone: str):
    req = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert req.status_code == 200, req.text
    resp = client.post(
        "/api/v1/auth/otp/verify",
        json={
            "phone": phone,
            "code": req.json()["dev_otp"],
            "dashboard_session": True,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp


def _dashboard_csrf(client: TestClient) -> dict[str, str]:
    resp = client.get("/api/v1/auth/csrf")
    assert resp.status_code == 200, resp.text
    return {DASHBOARD_CSRF_HEADER: resp.json()["csrf_token"]}


@pytest.mark.parametrize(
    "secret",
    [
        "change-me-generate-a-long-random-string",
        "short-secret",
        "0" * 32,
    ],
)
def test_non_local_runtime_rejects_weak_jwt_secrets(secret: str) -> None:
    with pytest.raises(SystemExit, match="JWT_SECRET"):
        Settings(app_env="prod", jwt_secret=secret).validate_for_runtime()


def test_non_local_runtime_accepts_strong_jwt_secret(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("JWT_SECRET", "strong-slice-18-secret-material-for-runtime")
    get_settings.cache_clear()
    app = create_app()
    assert app.title
    get_settings.cache_clear()


def test_otp_login_happy_path(client: TestClient, make_user, login) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110001")
    tokens = login(user.phone)
    assert tokens["token_type"] == "bearer"

    me = client.get("/api/v1/auth/me", headers=_bearer(tokens["access_token"]))
    assert me.status_code == 200
    body = me.json()
    assert body["phone"] == user.phone
    assert body["role"] == Role.DOCTOR.value


def test_dashboard_login_issues_http_only_cookie_session(
    client: TestClient, make_user
) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110011")
    resp = _dashboard_login(client, user.phone)

    body = resp.json()
    assert body["session_transport"] == "cookie"
    assert body["access_token"] is None
    assert body["refresh_token"] is None
    assert client.cookies.get(DASHBOARD_ACCESS_COOKIE)
    assert client.cookies.get(DASHBOARD_REFRESH_COOKIE)
    assert client.cookies.get(DASHBOARD_SESSION_COOKIE)

    set_cookie = ", ".join(resp.headers.get_list("set-cookie")).lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "path=/api/v1" in set_cookie
    assert "path=/api/v1/auth" in set_cookie

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["phone"] == user.phone


def test_dashboard_cookie_writes_require_csrf(client: TestClient, make_user) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110012")
    _dashboard_login(client, user.phone)

    denied = client.post(
        "/api/v1/devices/push-token",
        json={"token": "token-123456", "platform": "web"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "http_403"

    allowed = client.post(
        "/api/v1/devices/push-token",
        json={"token": "token-123456", "platform": "web"},
        headers=_dashboard_csrf(client),
    )
    assert allowed.status_code == 200


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


def test_verified_phone_without_account_is_offered_registration(
    client: TestClient,
) -> None:
    client.post("/api/v1/auth/otp/request", json={"phone": "+15559990000"})
    req = client.post("/api/v1/auth/otp/request", json={"phone": "+15559990000"})
    code = req.json()["dev_otp"]
    resp = client.post(
        "/api/v1/auth/otp/verify", json={"phone": "+15559990000", "code": code}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["registration_required"] is True
    assert body["registration_token"]
    assert body["access_token"] is None


def test_otp_request_rate_limit_audits_and_skips_sms(
    client: TestClient, session: Session, monkeypatch
) -> None:
    class _Gateway:
        def __init__(self) -> None:
            self.sent: list[str] = []

        def send_sms(self, *, to: str, body: str) -> str:
            self.sent.append(to)
            return "stub-sms"

    gateway = _Gateway()
    monkeypatch.setattr(auth_service, "get_notification_gateway", lambda: gateway)
    phone = "+15551110101"
    for _ in range(3):
        resp = client.post("/api/v1/auth/otp/request", json={"phone": phone})
        assert resp.status_code == 200

    limited = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "otp_rate_limited"
    assert gateway.sent == [phone, phone, phone]

    entry = session.exec(
        select(AuditLog).where(AuditLog.action == AuditAction.OTP_RATE_LIMITED.value)
    ).one()
    assert entry.meta["limit_kind"] == "phone_request"
    assert phone not in str(entry.meta)


def test_otp_request_rate_limits_by_source_ip(client: TestClient) -> None:
    for i in range(30):
        resp = client.post(
            "/api/v1/auth/otp/request",
            json={"phone": f"+1555222{i:04d}"},
        )
        assert resp.status_code == 200

    limited = client.post(
        "/api/v1/auth/otp/request",
        json={"phone": "+15552229999"},
    )
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "otp_rate_limited"


def test_otp_verify_rate_limit_precedes_code_attempts(
    client: TestClient, make_user, session: Session, monkeypatch
) -> None:
    phone = "+15551110102"
    make_user(Role.RESIDENT, phone=phone)
    monkeypatch.setenv("OTP_MAX_ATTEMPTS", "20")
    get_settings.cache_clear()
    req = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    assert req.status_code == 200
    otp = session.exec(select(OtpCode).where(OtpCode.phone == phone)).one()

    for _ in range(10):
        bad = client.post(
            "/api/v1/auth/otp/verify", json={"phone": phone, "code": "000000"}
        )
        assert bad.status_code == 401

    session.refresh(otp)
    before = otp.attempts
    limited = client.post(
        "/api/v1/auth/otp/verify", json={"phone": phone, "code": "000000"}
    )
    session.refresh(otp)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "otp_rate_limited"
    assert otp.attempts == before
    get_settings.cache_clear()


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


def test_dashboard_cookie_refresh_rotates_and_reuse_revokes_family(
    client: TestClient, make_user
) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110013")
    _dashboard_login(client, user.phone)
    csrf = _dashboard_csrf(client)
    first_refresh = client.cookies.get(DASHBOARD_REFRESH_COOKIE)
    assert first_refresh

    missing_csrf = client.post("/api/v1/auth/refresh")
    assert missing_csrf.status_code == 403

    rotated = client.post("/api/v1/auth/refresh", headers=csrf)
    assert rotated.status_code == 200
    assert rotated.json() == {"session_transport": "cookie"}
    current_refresh = client.cookies.get(DASHBOARD_REFRESH_COOKIE)
    assert current_refresh and current_refresh != first_refresh

    reuse = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": first_refresh},
    )
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "refresh_reuse_detected"

    after = client.post("/api/v1/auth/refresh", headers=csrf)
    assert after.status_code == 401


def test_rotation_is_consistent_and_replay_kills_new_token(
    client: TestClient, make_user, login, session: Session
) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110007")
    tokens = login(user.phone)
    refresh_a = tokens["refresh_token"]

    def _user_tokens() -> list[RefreshToken]:
        uid = session.exec(
            select(User.id).where(User.phone == "+15551110007")
        ).first()
        session.expire_all()
        return list(
            session.exec(select(RefreshToken).where(RefreshToken.user_id == uid)).all()
        )

    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a})
    assert r.status_code == 200

    # Old revoked + new present together: never "old revoked, new missing".
    rows = _user_tokens()
    assert len(rows) == 2
    assert sum(1 for t in rows if t.revoked_at is None) == 1

    # Replaying the old token must revoke the whole family — including the
    # winner's freshly issued token.
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_a})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_reuse_detected"
    assert all(t.revoked_at is not None for t in _user_tokens())


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


def test_dashboard_cookie_logout_revokes_refresh_and_clears_cookies(
    client: TestClient, make_user
) -> None:
    user = make_user(Role.DOCTOR, phone="+15551110014")
    _dashboard_login(client, user.phone)
    csrf = _dashboard_csrf(client)
    raw_refresh = client.cookies.get(DASHBOARD_REFRESH_COOKIE)
    assert raw_refresh

    denied = client.post("/api/v1/auth/logout")
    assert denied.status_code == 403

    out = client.post("/api/v1/auth/logout", headers=csrf)
    assert out.status_code == 204
    assert client.cookies.get(DASHBOARD_ACCESS_COOKIE) is None
    assert client.cookies.get(DASHBOARD_REFRESH_COOKIE) is None
    assert client.cookies.get(DASHBOARD_SESSION_COOKIE) is None
    assert (
        client.post("/api/v1/auth/refresh", json={"refresh_token": raw_refresh}).status_code
        == 401
    )


def test_dashboard_cors_allows_only_configured_credentialed_origin(
    client: TestClient,
) -> None:
    allowed = client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"

    denied = client.options(
        "/api/v1/auth/me",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in denied.headers
