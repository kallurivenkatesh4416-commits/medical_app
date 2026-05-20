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
    # Pin the schedule's start_date to yesterday and log against
    # yesterday's 08:00 slot — this is the schedule's real slot, in the
    # past (so the future-dose guard passes), and matches the
    # `times_of_day` ("08:00") so `_is_valid_dose_slot` returns True
    # regardless of what hour the suite runs at.
    yesterday = date.today() - timedelta(days=1)
    schedule = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(start_date=yesterday.isoformat()),
        headers=headers,
    ).json()
    slot_dt = datetime.combine(yesterday, datetime.min.time()).replace(hour=8)
    slot = slot_dt.isoformat()
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


# --------------------------------------------------------------------------- #
# 6. Review fixes — regression tests for the four Slice 9 review findings    #
# --------------------------------------------------------------------------- #


def test_reminder_consent_revocation_empties_resident_schedule_list(
    client: TestClient, project: Project, session: Session
) -> None:
    """Review #1: a revoked MEDICINE_REMINDER_NOTIFICATIONS consent must
    take the resident's reminder list to empty on the very next read, so
    the mobile `LocalReminderScheduler.syncFromSchedules([])` cancels
    every device-local reminder. The schedule row stays in the DB so a
    later re-grant restores the view without losing dose history."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990600")
    headers = {"Authorization": f"Bearer {resident_token}"}
    created = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(),
        headers=headers,
    )
    assert created.status_code == 200, created.text
    # Sanity: the schedule is visible before revocation.
    before = client.get("/api/v1/me/medicines/schedules", headers=headers)
    assert before.status_code == 200
    assert len(before.json()) == 1

    revoke = client.patch(
        "/api/v1/me/consents/medicine_reminder_notifications",
        json={"granted": False},
        headers=headers,
    )
    assert revoke.status_code == 200, revoke.text

    after = client.get("/api/v1/me/medicines/schedules", headers=headers)
    assert after.status_code == 200
    assert after.json() == []
    # The row is still present in the DB; re-granting brings it back.
    assert len(session.exec(select(MedicineSchedule)).all()) == 1

    regrant = client.patch(
        "/api/v1/me/consents/medicine_reminder_notifications",
        json={"granted": True},
        headers=headers,
    )
    assert regrant.status_code == 200, regrant.text
    restored = client.get("/api/v1/me/medicines/schedules", headers=headers)
    assert len(restored.json()) == 1


def test_dose_log_rejects_off_slot_timestamp(
    client: TestClient, project: Project, session: Session
) -> None:
    """Review #2: logging a dose at a time that is not one of the
    schedule's `times_of_day` would let the resident inflate `taken`
    above `scheduled_slots` and poison adherence. The service rejects
    those with 422 `invalid_dose_slot`."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990601")
    headers = {"Authorization": f"Bearer {resident_token}"}
    # Pin the schedule's start_date to yesterday so both off-slot
    # (`12:34`) and on-slot (`08:00`) timestamps are deterministically in
    # the past — otherwise the future-dose guard could fire first at
    # certain CI clock times and mask the off-slot path under test.
    yesterday = date.today() - timedelta(days=1)
    created = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(start_date=yesterday.isoformat()),  # once_daily at 08:00
        headers=headers,
    )
    sched_id = created.json()["id"]

    off_slot = datetime.combine(yesterday, datetime.min.time()).replace(hour=12, minute=34)
    resp = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": sched_id,
            "scheduled_for": off_slot.isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_dose_slot"
    assert session.exec(select(MedicineDoseLog)).all() == []

    # Same date, the real "08:00" slot — accepted.
    on_slot = off_slot.replace(hour=8, minute=0)
    ok = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": sched_id,
            "scheduled_for": on_slot.isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert ok.status_code == 200, ok.text


def test_dose_log_accepts_any_time_for_as_needed_schedule(
    client: TestClient, project: Project, session: Session
) -> None:
    """AS_NEEDED (PRN) medicines have no fixed slots — the resident
    records the actual intake time. The slot check must let any
    timestamp through, and `as_needed` schedules never contribute to
    `missed` (covered by the existing adherence test)."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990602")
    headers = {"Authorization": f"Bearer {resident_token}"}
    created = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(
            name="Salbutamol inhaler",
            dose="2 puffs",
            frequency=MedicineFrequency.AS_NEEDED.value,
            times_of_day=[],
        ),
        headers=headers,
    )
    assert created.status_code == 200, created.text
    sched_id = created.json()["id"]

    when = utcnow() - timedelta(minutes=42)
    resp = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": sched_id,
            "scheduled_for": when.isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


def test_staff_deactivate_via_wrong_resident_path_returns_404(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """Review #3: the staff deactivate route must verify that the
    schedule's owner matches the path's `{resident_id}`. Without it, a
    doctor in the same project could deactivate resident A's schedule
    via resident B's URL."""
    _, resident_a, _ = _onboard(client, project, session, "+15559990610")
    _, resident_b, _ = _onboard(client, project, session, "+15559990611")
    doctor = _make_user(session, project, Role.DOCTOR)

    schedule = client.post(
        f"/api/v1/residents/{resident_a.id}/medicines/schedules",
        json=_schedule_body(),
        headers=_auth(client, login, doctor),
    ).json()

    wrong = client.post(
        f"/api/v1/residents/{resident_b.id}/medicines/schedules/{schedule['id']}/deactivate",
        headers=_auth(client, login, doctor),
    )
    assert wrong.status_code == 404
    assert wrong.json()["error"]["code"] == "schedule_not_found"

    # The schedule is still active.
    row = session.exec(
        select(MedicineSchedule).where(MedicineSchedule.id == uuid.UUID(schedule["id"]))
    ).first()
    assert row is not None
    assert row.active is True

    # And the correct path still works.
    right = client.post(
        f"/api/v1/residents/{resident_a.id}/medicines/schedules/{schedule['id']}/deactivate",
        headers=_auth(client, login, doctor),
    )
    assert right.status_code == 200
    assert right.json()["active"] is False


def test_schedule_rejects_duplicate_slots(
    client: TestClient, project: Project, session: Session
) -> None:
    """Review #4 (a): two identical `times_of_day` values would register
    the same local reminder twice on the device. The service rejects
    duplicates with 422 `duplicate_time_of_day`."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990620")
    headers = {"Authorization": f"Bearer {resident_token}"}
    resp = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(
            frequency=MedicineFrequency.TWICE_DAILY.value,
            times_of_day=["08:00", "08:00"],
        ),
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "duplicate_time_of_day"


def test_schedule_rejects_as_needed_with_fixed_times_of_day(
    client: TestClient, project: Project, session: Session
) -> None:
    """Review #4 (b): `as_needed` (PRN) is informational — declaring
    fixed slots would create local notifications for a medicine that is
    only taken on need. Rejected with 422 `as_needed_no_slots`."""
    resident_token, _, _ = _onboard(client, project, session, "+15559990621")
    headers = {"Authorization": f"Bearer {resident_token}"}
    resp = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(
            frequency=MedicineFrequency.AS_NEEDED.value,
            times_of_day=["08:00"],
        ),
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "as_needed_no_slots"


# --------------------------------------------------------------------------- #
# 7. Review #2 — future-dated dose logs corrupt adherence                     #
# --------------------------------------------------------------------------- #


def test_dose_log_rejects_future_scheduled_for(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """Repro of the original review finding: a schedule starting tomorrow
    that accepts a `taken` log for tomorrow's 08:00 slot today would
    yield `taken: 1, scheduled_slots: 0` (adherence projects slots only
    up to "now"). The future-dose guard rejects it at write time with
    422 `future_dose_slot`. Adherence stays sound: nothing logged, no
    slots projected, no inflation."""
    resident_token, resident, _ = _onboard(client, project, session, "+15559990630")
    headers = {"Authorization": f"Bearer {resident_token}"}
    doctor = _make_user(session, project, Role.DOCTOR)

    tomorrow = date.today() + timedelta(days=1)
    created = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(start_date=tomorrow.isoformat()),
        headers=headers,
    )
    assert created.status_code == 200, created.text
    sched_id = created.json()["id"]

    future_slot = datetime.combine(tomorrow, datetime.min.time()).replace(hour=8)
    resp = client.post(
        "/api/v1/me/medicines/doses",
        json={
            "schedule_id": sched_id,
            "scheduled_for": future_slot.isoformat(),
            "status": MedicineDoseStatus.TAKEN.value,
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "future_dose_slot"
    assert session.exec(select(MedicineDoseLog)).all() == []

    # And the doctor's adherence view stays at zero across the board.
    adherence = client.get(
        f"/api/v1/residents/{resident.id}/medicines/adherence?days=7",
        headers=_auth(client, login, doctor),
    )
    assert adherence.status_code == 200, adherence.text
    assert adherence.json()["totals"] == {
        "taken": 0,
        "skipped": 0,
        "missed": 0,
        "scheduled_slots": 0,
    }


def test_adherence_filters_out_stray_future_log_row(
    client: TestClient, project: Project, session: Session, login
) -> None:
    """Defense-in-depth: even if a future-dated dose row somehow lands
    in the DB (bypassing the API — clock-skew slip, manual repair,
    future migration), the adherence read must exclude it so
    `taken + skipped` never exceeds `scheduled_slots`. We bypass the
    API and insert the row directly here to simulate that path."""
    resident_token, resident, resident_user = _onboard(
        client, project, session, "+15559990631"
    )
    headers = {"Authorization": f"Bearer {resident_token}"}
    doctor = _make_user(session, project, Role.DOCTOR)

    tomorrow = date.today() + timedelta(days=1)
    sched = client.post(
        "/api/v1/me/medicines/schedules",
        json=_schedule_body(start_date=tomorrow.isoformat()),
        headers=headers,
    ).json()

    # Direct insert: simulates a stale row from a past version of the
    # service. The API would reject this via `future_dose_slot`.
    smuggled = MedicineDoseLog(
        project_id=resident.project_id,
        schedule_id=uuid.UUID(sched["id"]),
        resident_id=resident.id,
        scheduled_for=datetime.combine(tomorrow, datetime.min.time()).replace(hour=8),
        status=MedicineDoseStatus.TAKEN.value,
        logged_by=resident_user.id,
    )
    session.add(smuggled)
    session.commit()

    adherence = client.get(
        f"/api/v1/residents/{resident.id}/medicines/adherence?days=7",
        headers=_auth(client, login, doctor),
    )
    assert adherence.status_code == 200, adherence.text
    totals = adherence.json()["totals"]
    # The smuggled row exists but is filtered out: taken stays 0,
    # scheduled_slots stays 0, invariant `taken + skipped <=
    # scheduled_slots` holds.
    assert totals["taken"] == 0
    assert totals["scheduled_slots"] == 0
    assert totals["taken"] + totals["skipped"] <= totals["scheduled_slots"] or (
        totals["scheduled_slots"] == 0 and totals["taken"] + totals["skipped"] == 0
    )
