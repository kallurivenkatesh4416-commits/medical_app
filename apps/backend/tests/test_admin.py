"""Slice 10 — aggregate-only admin KPIs and monthly PDF/CSV export."""

import uuid
from datetime import date, datetime, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlmodel import Session, select

from app.enums import AuditAction, CaseStatus, MedicineDoseStatus, MedicineFrequency, Role
from app.models.audit import AuditLog
from app.models.base import utcnow
from app.models.emergency import EmergencyCase
from app.models.medical_record import MedicalRecord
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
    }


def _onboard(
    client: TestClient, project: Project, session: Session, phone: str
) -> tuple[str, Resident, User]:
    tok = _register_token(client, phone)
    resp = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(str(project.id)),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 200, resp.text
    user = session.exec(select(User).where(User.phone == phone)).first()
    assert user is not None
    resident = session.exec(select(Resident).where(Resident.user_id == user.id)).first()
    assert resident is not None
    return resp.json()["access_token"], resident, user


def _auth(client: TestClient, login, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(user.phone)['access_token']}"}


def _seed_case_and_record(
    session: Session, *, project: Project, resident: Resident, user: User
) -> None:
    alert_time = utcnow() - timedelta(minutes=10)
    case = EmergencyCase(
        project_id=project.id,
        resident_id=resident.id,
        created_by_user_id=user.id,
        status=CaseStatus.CLOSED.value,
        alert_time=alert_time,
        acknowledged_at=alert_time + timedelta(seconds=90),
        on_site_at=alert_time + timedelta(seconds=420),
        closed_at=alert_time + timedelta(seconds=600),
        symptom_codes=["fall"],
    )
    record = MedicalRecord(
        resident_id=resident.id,
        project_id=project.id,
        storage_key=uuid.uuid4().hex,
        file_name="lab.pdf",
        content_type="application/pdf",
        record_type="lab",
        size_bytes=128,
        uploaded_by=user.id,
    )
    session.add(case)
    session.add(record)
    session.commit()


def _seed_medicine(
    client: TestClient,
    *,
    token: str,
    revoke_after_log: bool = False,
) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    yesterday = date.today() - timedelta(days=1)
    schedule = client.post(
        "/api/v1/me/medicines/schedules",
        json={
            "name": "Amlodipine",
            "dose": "5 mg",
            "instructions": "after breakfast",
            "frequency": MedicineFrequency.TWICE_DAILY.value,
            "times_of_day": ["08:00", "20:00"],
            "start_date": yesterday.isoformat(),
            "end_date": None,
        },
        headers=headers,
    )
    assert schedule.status_code == 200, schedule.text
    taken_slot = datetime.combine(yesterday, datetime.min.time()).replace(hour=8)
    logged = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": schedule.json()["id"],
            "scheduled_for": taken_slot.isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert logged.status_code == 200, logged.text
    if revoke_after_log:
        revoked = client.patch(
            "/api/v1/me/consents/medicine_reminder_notifications",
            json={"granted": False},
            headers=headers,
        )
        assert revoked.status_code == 200, revoked.text


def test_admin_kpis_are_aggregate_builder_visible_and_consent_aware(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, resident, resident_user = _onboard(
        client, project, session, "+15559990700"
    )
    paused_token, _, _ = _onboard(client, project, session, "+15559990701")
    builder = make_user(Role.BUILDER_ADMIN, phone="+15559991700")

    _seed_case_and_record(session, project=project, resident=resident, user=resident_user)
    _seed_medicine(client, token=resident_token)
    _seed_medicine(client, token=paused_token, revoke_after_log=True)

    resp = client.get(
        "/api/v1/admin/kpis?days=7",
        headers=_auth(client, login, builder),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["emergency"]["total_cases"] == 1
    assert body["emergency"]["closed_cases"] == 1
    assert body["emergency"]["average_ack_seconds"] == 90
    assert body["residents"]["onboarded"] == 2
    assert body["records"]["uploaded"] == 1
    assert body["medicines"]["scheduled_taken"] == 1
    assert body["medicines"]["consented_residents"] == 1
    assert body["medicines"]["consent_paused_residents"] == 1
    assert body["medicines"]["scheduled_slots"] > 0
    assert "Asha Rao" not in resp.text
    assert "B-1203" not in resp.text
    assert "Amlodipine" not in resp.text
    assert str(resident.id) not in resp.text
    assert AuditAction.ADMIN_KPI_READ.value in list(
        session.exec(select(AuditLog.action)).all()
    )


def test_admin_monthly_exports_are_direct_aggregate_files(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    token, resident, resident_user = _onboard(client, project, session, "+15559990710")
    builder = make_user(Role.BUILDER_ADMIN, phone="+15559991710")
    _seed_case_and_record(session, project=project, resident=resident, user=resident_user)
    _seed_medicine(client, token=token)

    month = date.today().strftime("%Y-%m")
    headers = _auth(client, login, builder)
    csv_resp = client.get(
        f"/api/v1/admin/exports/monthly?month={month}&format=csv",
        headers=headers,
    )
    assert csv_resp.status_code == 200, csv_resp.text
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_resp.headers["content-disposition"]
    csv_text = csv_resp.text
    assert "emergency,total_cases,1" in csv_text
    assert "medicines,scheduled_taken,1" in csv_text
    assert "Asha Rao" not in csv_text
    assert str(resident.id) not in csv_text

    pdf_resp = client.get(
        f"/api/v1/admin/exports/monthly?month={month}&format=pdf",
        headers=headers,
    )
    assert pdf_resp.status_code == 200, pdf_resp.text
    assert pdf_resp.headers["content-type"] == "application/pdf"
    text = "\n".join(
        page.extract_text()
        for page in PdfReader(BytesIO(pdf_resp.content)).pages
    )
    assert "Monthly Admin KPI Export" in text
    assert "Emergency Cases" in text
    assert "Asha Rao" not in text
    assert str(resident.id) not in text

    actions = list(session.exec(select(AuditLog.action)).all())
    assert actions.count(AuditAction.ADMIN_EXPORT_GENERATED.value) == 2
