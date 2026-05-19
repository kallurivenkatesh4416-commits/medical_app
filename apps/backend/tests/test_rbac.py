"""Slice 2 — RBAC: auth required, role gating, and the PHI-role block
(builder_admin + security_desk can never reach patient data)."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.enums import Role
from app.security.deps import forbid_phi_roles, get_current_user, require_roles


@pytest.fixture
def app_with_guarded_routes(client: TestClient) -> FastAPI:
    app = client.app

    @app.get("/_t/doctor-only", dependencies=[Depends(require_roles(Role.DOCTOR))])
    def _doctor_only() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/_t/phi")
    def _phi(_=Depends(forbid_phi_roles)) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/_t/whoami")
    def _whoami(user=Depends(get_current_user)) -> dict[str, str]:
        return {"role": user.role}

    return app


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_is_401(
    client: TestClient, app_with_guarded_routes
) -> None:
    assert client.get("/_t/whoami").status_code == 401
    assert client.get("/_t/whoami", headers=_hdr("garbage")).status_code == 401


def test_role_gate_allows_and_denies(
    client: TestClient, app_with_guarded_routes, make_user, login
) -> None:
    doctor = make_user(Role.DOCTOR, phone="+15552220001")
    resident = make_user(Role.RESIDENT, phone="+15552220002")

    d_tok = login(doctor.phone)["access_token"]
    r_tok = login(resident.phone)["access_token"]

    assert client.get("/_t/doctor-only", headers=_hdr(d_tok)).status_code == 200
    assert client.get("/_t/doctor-only", headers=_hdr(r_tok)).status_code == 403


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.DOCTOR, 200),
        (Role.NURSE, 200),
        (Role.BUILDER_ADMIN, 403),
        (Role.SECURITY_DESK, 403),
    ],
)
def test_phi_role_block(
    client: TestClient, app_with_guarded_routes, make_user, login, role, expected
) -> None:
    user = make_user(role)
    tok = login(user.phone)["access_token"]
    assert client.get("/_t/phi", headers=_hdr(tok)).status_code == expected
