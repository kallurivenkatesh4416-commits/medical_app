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


PHI_ROUTE_PROBES = [
    ("GET", "/api/v1/me/profile", None),
    ("GET", "/api/v1/me/records", None),
    ("GET", "/api/v1/me/records/00000000-0000-0000-0000-000000000001/link", None),
    (
        "POST",
        "/api/v1/me/records",
        {
            "data": {"record_type": "lab"},
            "files": {"file": ("lab.pdf", b"%PDF-1.4\n", "application/pdf")},
        },
    ),
    ("GET", "/api/v1/me/medicines/schedules", None),
    (
        "POST",
        "/api/v1/me/medicines/schedules",
        {
            "json": {
                "name": "Amlodipine",
                "frequency": "once_daily",
                "times_of_day": ["08:00"],
                "start_date": "2026-05-19",
            }
        },
    ),
    (
        "POST",
        "/api/v1/me/medicines/doses",
        {
            "json": {
                "schedule_id": "00000000-0000-0000-0000-000000000001",
                "scheduled_for": "2026-05-19T08:00:00Z",
                "status": "taken",
            }
        },
    ),
    ("GET", "/api/v1/residents/00000000-0000-0000-0000-000000000001/profile", None),
    ("GET", "/api/v1/residents/00000000-0000-0000-0000-000000000001/records", None),
    (
        "GET",
        "/api/v1/residents/00000000-0000-0000-0000-000000000001/records/"
        "00000000-0000-0000-0000-000000000002/link",
        None,
    ),
    ("GET", "/api/v1/emergency/alerts/active", None),
    ("GET", "/api/v1/emergency/alerts/00000000-0000-0000-0000-000000000001", None),
    (
        "POST",
        "/api/v1/emergency/alerts/00000000-0000-0000-0000-000000000001/transition",
        {"json": {"target_status": "acknowledged"}},
    ),
    (
        "POST",
        "/api/v1/emergency/alerts/00000000-0000-0000-0000-000000000001/vitals",
        {"json": {"heart_rate_bpm": 80}},
    ),
    (
        "POST",
        "/api/v1/emergency/alerts/00000000-0000-0000-0000-000000000001/notes",
        {"json": {"note_type": "observation", "body": "blocked"}},
    ),
    (
        "POST",
        "/api/v1/emergency/alerts/00000000-0000-0000-0000-000000000001/handover",
        {
            "json": {
                "hospital_destination": "Hospital",
                "doctor_registration_number": "MCI-1",
            }
        },
    ),
    ("GET", "/api/v1/handover/00000000-0000-0000-0000-000000000001/link", None),
    (
        "POST",
        "/api/v1/handover/00000000-0000-0000-0000-000000000001/dispatch",
        {"json": {"email": "er@example.com", "whatsapp": None}},
    ),
    (
        "POST",
        "/api/v1/residents/00000000-0000-0000-0000-000000000001/medicines/schedules",
        {
            "json": {
                "name": "Metformin",
                "frequency": "once_daily",
                "times_of_day": ["08:00"],
                "start_date": "2026-05-19",
            }
        },
    ),
    ("GET", "/api/v1/residents/00000000-0000-0000-0000-000000000001/medicines/schedules", None),
    (
        "POST",
        "/api/v1/residents/00000000-0000-0000-0000-000000000001/medicines/schedules/"
        "00000000-0000-0000-0000-000000000002/deactivate",
        None,
    ),
    ("GET", "/api/v1/residents/00000000-0000-0000-0000-000000000001/medicines/adherence", None),
]


@pytest.mark.parametrize("role", [Role.BUILDER_ADMIN, Role.SECURITY_DESK])
@pytest.mark.parametrize(("method", "path", "kwargs"), PHI_ROUTE_PROBES)
def test_non_phi_roles_are_blocked_from_real_phi_routes(
    client: TestClient, make_user, login, role, method, path, kwargs
) -> None:
    user = make_user(role)
    tok = login(user.phone)["access_token"]
    response = client.request(method, path, headers=_hdr(tok), **(kwargs or {}))
    assert response.status_code == 403, f"{method} {path} returned {response.status_code}"
