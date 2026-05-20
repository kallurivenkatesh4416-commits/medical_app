"""Slice 9 — medicine reminders: schedule CRUD with RBAC + tenant + consent
gating, resident self-only dose logging, adherence summary for staff with
live-computed missed count, and the Slice 8 handover §6 wire-in showing
the new schedules in the generated PDF.
"""

import uuid
from datetime import date, datetime, timedelta
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlmodel import Session, select

from app.enums import AuditAction, MedicineDoseStatus, MedicineFrequency, Role
from app.models.audit import AuditLog
from app.models.base import utcnow
from app.models.medicine import MedicineDoseLog, MedicineSchedule
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User

# --------------------------------------------------------------------------- #
# Helpers (mirror tests/test_handover.py / test_emergency.py shape)            #
# --------------------------------------------------------------------------- #


def _register_token(client: TestClient, phone: str) -> str:
    req = client.post("/api/v1/auth/otp/request", json={"phone": phone})
    code = req.json()["dev_otp"]
    vr = client.post("/api/v1/auth/otp/verify", json={"phone": phone, "code": code})
    assert vr.status_code == 200, vr.text
    return vr.json()["registration_token"]


def _payload(
    project_id: str, *, medicine_reminder_consent: bool = True
) -> dict:
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
            {
                "consent_type": "medicine_reminder_notifications",
                "granted": medicine_reminder_consent,
            },
        ],
        "blood_group": "O+",
        "diseases": ["hypertension"],
        "allergies": ["penicillin"],
    }


def _onboard(
    client: TestClient,
    project: Project,
    session: Session,
    phone: str,
    *,
    medicine_reminder_consent: bool = True,
) -> tuple[str, Resident, User]:
    tok = _register_token(client, phone)
    resp = client.post(
        "/api/v1/onboarding/complete",
        json=_payload(
            str(project.id),
            medicine_reminder_consent=medicine_reminder_consent,
        ),
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 200, resp.text
    user = session.exec(select(User).where(User.phone == phone)).first()
    resident = session.exec(select(Resident).where(Resident.user_id == user.id)).first()
    return resp.json()["access_token"], resident, user


def _auth(client: TestClient, login, user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(user.phone)['access_token']}"}


def _make_user(session: Session, project: Project, role: Role) -> User:
    u = User(
        project_id=project.id,
        phone=f"+15559{uuid.uuid4().int % 100000:05d}",
        role=role.value,
        full_name=f"Test {role.value}",
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _schedule_body(**overrides) -> dict:
    body: dict = {
        "name": "Amlodipine",
        "dose": "5 mg",
        "instructions": "after breakfast",
        "frequency": MedicineFrequency.ONCE_DAILY.value,
        "times_of_day": ["08:00"],
        "start_date": date.today().isoformat(),
        "end_date": None,
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# 1. Schedule create / list / deactivate — resident self path                  #
# --------------------------------------------------------------------------- #


def test_resident_creates_lists_and_deactivates_own_schedule(
    client: TestClient, project: Project, session: Session
) -> None:
    resident_token, resident, _ = _onboard(client, project, session, "+15559990500")
    headers = {"Authorization": f"Bearer {resident_token}"}

    created = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(),
        headers=headers,
    )
    assert created.status_code == 200, created.text
    sched = created.json()
    assert sched["frequency"] == "once_daily"
    assert sched["times_of_day"] == ["08:00"]
    assert sched["active"] is True
    assert sched["resident_id"] == str(resident.id)

    listed = client.get("/api/v1/me/medicines/schedules", headers=headers)
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == sched["id"]

    deactivated = client.post(
        f"/api/v1/me/medicines/schedules/{sched['id']}/deactivate",
        headers=headers,
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False

    actions = [row.action for row in session.exec(select(AuditLog)).all()]
    assert AuditAction.MEDICINE_SCHEDULE_CREATED.value in actions
    assert AuditAction.MEDICINE_SCHEDULE_LIST.value in actions
    assert AuditAction.MEDICINE_SCHEDULE_UPDATED.value in actions


def test_invalid_times_of_day_count_is_422(
    client: TestClient, project: Project, session: Session
) -> None:
    """`twice_daily` requires exactly 2 slots; the service rejects mismatches
    so the mobile reminder scheduler never falls out of sync with the
    declared frequency."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990501")
    headers = {"Authorization": f"Bearer {resident_token}"}

    resp = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(
            frequency=MedicineFrequency.TWICE_DAILY.value,
            times_of_day=["08:00"],  # only one slot
        ),
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "times_of_day_mismatch"


def test_schedule_creation_blocked_when_reminder_consent_declined(
    client: TestClient, project: Project, session: Session
) -> None:
    """A resident who declined MEDICINE_REMINDER_NOTIFICATIONS at onboarding
    cannot create a schedule; assert_consent reads live state."""
    resident_token, _, _ = _onboard(
        client,
        project,
        session,
        "+15559990502",
        medicine_reminder_consent=False,
    )
    headers = {"Authorization": f"Bearer {resident_token}"}

    resp = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(),
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "consent_required"
    # No schedule row was persisted.
    assert session.exec(select(MedicineSchedule)).all() == []


def test_schedule_creation_blocked_after_revocation(
    client: TestClient, project: Project, session: Session
) -> None:
    """Live consent state — granting at onboarding then revoking later
    must stop new schedules immediately."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990503")
    headers = {"Authorization": f"Bearer {resident_token}"}

    revoke = client.patch(
        "/api/v1/me/consents/medicine_reminder_notifications",
        json={"granted": False},
        headers=headers,
    )
    assert revoke.status_code == 200, revoke.text

    resp = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(),
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "consent_required"


# --------------------------------------------------------------------------- #
# 2. Doctor-on-behalf path + RBAC                                              #
# --------------------------------------------------------------------------- #


def test_doctor_creates_schedule_for_resident_and_nurse_can_list(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, resident, _ = _onboard(client, project, session, "+15559990510")
    doctor = _make_user(session, project, Role.DOCTOR)
    nurse = _make_user(session, project, Role.NURSE)

    created = client.post(
        f"/api/v1/residents/{resident.id}/medicines/schedules",
        json=_schedule_body(name="Metformin", dose="500 mg"),
        headers=_auth(client, login, doctor),
    )
    assert created.status_code == 200, created.text
    sched_id = created.json()["id"]
    assert created.json()["prescribed_by"] == str(doctor.id)

    listed = client.get(
        f"/api/v1/residents/{resident.id}/medicines/schedules",
        headers=_auth(client, login, nurse),
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == sched_id


def test_phi_blocked_roles_forbidden_from_schedule_list_and_adherence(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, resident, _ = _onboard(client, project, session, "+15559990511")
    builder = _make_user(session, project, Role.BUILDER_ADMIN)

    listed = client.get(
        f"/api/v1/residents/{resident.id}/medicines/schedules",
        headers=_auth(client, login, builder),
    )
    assert listed.status_code == 403

    adherence = client.get(
        f"/api/v1/residents/{resident.id}/medicines/adherence",
        headers=_auth(client, login, builder),
    )
    assert adherence.status_code == 403


def test_doctor_from_other_project_gets_404(
    client: TestClient, project: Project, session: Session, login
) -> None:
    _, resident, _ = _onboard(client, project, session, "+15559990512")
    other = Project(name="Other Tower")
    session.add(other)
    session.commit()
    outsider = User(
        project_id=other.id,
        phone="+15559991500",
        role=Role.DOCTOR.value,
        full_name="Dr Stranger",
    )
    session.add(outsider)
    session.commit()

    resp = client.post(
        f"/api/v1/residents/{resident.id}/medicines/schedules",
        json=_schedule_body(),
        headers=_auth(client, login, outsider),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "resident_not_found"


# --------------------------------------------------------------------------- #
# 3. Dose log — resident self-only, idempotent on the slot                    #
# --------------------------------------------------------------------------- #


def test_resident_logs_taken_and_duplicate_log_returns_same_row(
    client: TestClient, project: Project, session: Session
) -> None:
    resident_token, _, _ = _onboard(client, project, session, "+15559990520")
    headers = {"Authorization": f"Bearer {resident_token}"}
    schedule = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(),
        headers=headers,
    ).json()

    slot = (utcnow() - timedelta(minutes=10)).isoformat()
    first = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": schedule["id"],
            "scheduled_for": slot,
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert first.status_code == 200, first.text
    log_id = first.json()["id"]
    assert first.json()["status"] == "taken"

    # Network-flake retry on the same slot must collide on the unique
    # (schedule_id, scheduled_for) index and return the existing row, not
    # create a duplicate.
    duplicate = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": schedule["id"],
            "scheduled_for": slot,
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["id"] == log_id
    rows = session.exec(select(MedicineDoseLog)).all()
    assert len(rows) == 1


def test_doctor_cannot_log_dose_for_resident(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """Only the resident knows whether they actually took the dose; the
    dose-log endpoint is `me` only. Doctor gets 403 (role) before any
    business logic."""
    _, _, _ = _onboard(client, project, session, "+15559990521")
    doctor = _make_user(session, project, Role.DOCTOR)

    resp = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": str(uuid.uuid4()),
            "scheduled_for": utcnow().isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 403


def test_dose_log_rejected_for_inactive_schedule(
    client: TestClient, project: Project, session: Session
) -> None:
    resident_token, _, _ = _onboard(client, project, session, "+15559990522")
    headers = {"Authorization": f"Bearer {resident_token}"}
    schedule = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(),
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/me/medicines/schedules/{schedule['id']}/deactivate",
        headers=headers,
    )
    resp = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": schedule["id"],
            "scheduled_for": utcnow().isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "schedule_inactive"


# --------------------------------------------------------------------------- #
# 4. Adherence summary — live-computed missed count                            #
# --------------------------------------------------------------------------- #


def test_doctor_reads_adherence_with_taken_skipped_and_computed_missed(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, resident, _ = _onboard(client, project, session, "+15559990530")
    headers = {"Authorization": f"Bearer {resident_token}"}
    doctor = _make_user(session, project, Role.DOCTOR)

    # Schedule started 3 days ago, twice daily — 6 slots in the past 3
    # days, all old enough to be beyond the missed grace if unlogged.
    start = (date.today() - timedelta(days=3))
    schedule_resp = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(
            name="Atorvastatin",
            dose="10 mg",
            frequency=MedicineFrequency.TWICE_DAILY.value,
            times_of_day=["08:00", "20:00"],
            start_date=start.isoformat(),
        ),
        headers=headers,
    )
    assert schedule_resp.status_code == 200, schedule_resp.text
    sched_id = schedule_resp.json()["id"]

    # Log: 1 taken, 1 skipped, the rest left missed.
    taken_slot = datetime.combine(start, datetime.min.time()).replace(hour=8)
    skipped_slot = datetime.combine(start, datetime.min.time()).replace(hour=20)
    client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": sched_id,
            "scheduled_for": taken_slot.isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": sched_id,
            "scheduled_for": skipped_slot.isoformat(),
            "status": MedicineDoseStatus.SKIPPED.value,
        },
        headers=headers,
    )

    resp = client.get(
        f"/api/v1/residents/{resident.id}/medicines/adherence?days=7",
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()
    assert summary["window_days"] == 7
    assert summary["totals"]["taken"] == 1
    assert summary["totals"]["skipped"] == 1
    # At least the 4 unlogged slots from the past 3 days should count as
    # missed (today's still-pending slots stay inside the grace).
    assert summary["totals"]["missed"] >= 4
    assert summary["schedules"][0]["name"] == "Atorvastatin"


def test_adherence_requires_emergency_share_with_doctor_consent(
    client: TestClient, project: Project, session: Session, login
) -> None:
    resident_token, resident, _ = _onboard(client, project, session, "+15559990531")
    headers = {"Authorization": f"Bearer {resident_token}"}
    doctor = _make_user(session, project, Role.DOCTOR)

    revoke = client.patch(
        "/api/v1/me/consents/emergency_share_with_doctor",
        json={"granted": False},
        headers=headers,
    )
    assert revoke.status_code == 200, revoke.text

    resp = client.get(
        f"/api/v1/residents/{resident.id}/medicines/adherence",
        headers=_auth(client, login, doctor),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "consent_required"


# --------------------------------------------------------------------------- #
# 5. Slice 8 handover §6 — populated from active schedules                    #
# --------------------------------------------------------------------------- #


def test_handover_section_6_lists_active_medicines(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """Slice 9 wire-in: the Slice 8 PDF §6 "Current Medicines" placeholder
    must now reflect the resident's active schedules."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990540")
    headers = {"Authorization": f"Bearer {resident_token}"}
    doctor = _make_user(session, project, Role.DOCTOR)

    create = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(
            name="Amlodipine",
            dose="5 mg",
            instructions="after breakfast",
        ),
        headers=headers,
    )
    assert create.status_code == 200, create.text

    # Generate the handover PDF and inspect its rendered text.
    alert = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["chest_pain"]},
        headers={**headers, "Idempotency-Key": "med-handover"},
    )
    assert alert.status_code == 200, alert.text
    handover = client.post(
        f"/api/v1/emergency/alerts/{alert.json()['id']}/handover",
        json={
            "hospital_destination": "Apollo Hospital",
            "doctor_registration_number": "MCI-9001",
        },
        headers=_auth(client, login, doctor),
    )
    assert handover.status_code == 200, handover.text

    pdf_resp = client.get(
        handover.json()["signed_url"].split("localhost:8000")[1]
    )
    assert pdf_resp.status_code == 200
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(pdf_resp.content)).pages)
    assert "Current Medicines" in text
    assert "Amlodipine" in text
    assert "5 mg" in text
    # The placeholder line MUST be gone when at least one schedule exists.
    assert "No current medicines on file" not in text


def test_handover_section_6_keeps_placeholder_with_no_active_schedules(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """No medicines on file → the section header still renders with the
    placeholder; the §8 layout must not shift between residents."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990541")
    doctor = _make_user(session, project, Role.DOCTOR)
    headers = {"Authorization": f"Bearer {resident_token}"}

    alert = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={**headers, "Idempotency-Key": "med-handover-empty"},
    )
    assert alert.status_code == 200, alert.text
    handover = client.post(
        f"/api/v1/emergency/alerts/{alert.json()['id']}/handover",
        json={
            "hospital_destination": "Apollo Hospital",
            "doctor_registration_number": "MCI-9002",
        },
        headers=_auth(client, login, doctor),
    )
    assert handover.status_code == 200, handover.text
    pdf_resp = client.get(
        handover.json()["signed_url"].split("localhost:8000")[1]
    )
    assert pdf_resp.status_code == 200
    text = "\n".join(p.extract_text() for p in PdfReader(BytesIO(pdf_resp.content)).pages)
    assert "Current Medicines" in text
    assert "No current medicines on file" in text
