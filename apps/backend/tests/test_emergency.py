"""Slice 5/6 — emergency alert: idempotent create, 3-channel fan-out,
on-call resolution, 60s backup escalation, and the mobile fallback sheet."""

import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session as OrmSession
from sqlmodel import Session, select

from app.enums import (
    AuditAction,
    CaseStatus,
    FallbackChannel,
    NotificationChannel,
    NotificationStatus,
    Role,
)
from app.models.audit import AuditLog
from app.models.base import utcnow
from app.models.emergency import (
    CaseEvent,
    DeviceToken,
    EmergencyCase,
    NotificationAttempt,
)
from app.models.on_call import OnCallSchedule
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User
from app.services import emergency_service


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


def _schedule(
    session: Session,
    project: Project,
    user: User,
    role: Role,
    *,
    backup: bool = False,
    phone: str | None = None,
) -> OnCallSchedule:
    now = utcnow()
    row = OnCallSchedule(
        project_id=project.id,
        role=role.value,
        user_id=user.id,
        starts_at=now - timedelta(seconds=60),
        ends_at=now + timedelta(hours=1),
        is_backup=backup,
        contact_phone=phone,
    )
    session.add(row)
    session.commit()
    return row


def _by_channel(session: Session) -> dict[str, list[NotificationAttempt]]:
    out: dict[str, list[NotificationAttempt]] = {}
    for a in session.exec(select(NotificationAttempt)).all():
        out.setdefault(a.channel, []).append(a)
    return out


def test_resident_alert_fans_out_three_channels_with_feed_and_audit(
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

    channels = {a["channel"]: a for a in body["notification_attempts"]}
    assert set(channels) == {
        NotificationChannel.FCM.value,
        NotificationChannel.SMS.value,
        NotificationChannel.VOICE.value,
    }
    for ch in channels.values():
        assert ch["status"] == NotificationStatus.SENT.value
        assert ch["recipient_id"] == str(doctor.id)

    feed = client.get("/api/v1/emergency/alerts/active", headers=doctor_headers)
    assert feed.status_code == 200, feed.text
    assert feed.json()[0]["id"] == body["id"]
    assert feed.json()[0]["resident_name"] == "Asha Rao"

    actions = list(session.exec(select(AuditLog.action)).all())
    assert AuditAction.EMERGENCY_ALERT_CREATED.value in actions
    assert AuditAction.EMERGENCY_ALERT_LIST.value in actions


def test_alert_requires_idempotency_and_exact_replay(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990002")
    make_user(Role.DOCTOR, phone="+15559991112")
    headers = {"Authorization": f"Bearer {resident_token}", "Idempotency-Key": "same"}

    missing = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "idempotency_key_required"

    first = client.post(
        "/api/v1/emergency/alerts", json={"symptom_codes": ["fall"]}, headers=headers
    )
    assert first.status_code == 200
    replay = client.post(
        "/api/v1/emergency/alerts", json={"symptom_codes": ["fall"]}, headers=headers
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
    _register_doctor_push(client, _auth(client, login, doctor))
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
            "/api/v1/emergency/alerts/active", headers=_auth(client, login, blocked)
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
        "/api/v1/emergency/alerts/active", headers=_auth(client, login, outsider)
    )
    assert cross.status_code == 200
    assert cross.json() == []


def test_killed_push_still_delivers_sms_and_voice(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    """No push token → FCM fails, but SMS + voice still fire independently."""
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

    by_channel = _by_channel(session)
    fcm = by_channel[NotificationChannel.FCM.value][0]
    assert fcm.status == NotificationStatus.FAILED.value
    assert fcm.error == "no_push_token"
    assert by_channel[NotificationChannel.SMS.value][0].status == "sent"
    assert by_channel[NotificationChannel.VOICE.value][0].status == "sent"


def test_one_channel_exception_does_not_block_the_others(
    client: TestClient, project: Project, session: Session, make_user, login, monkeypatch
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990010")
    doctor = make_user(Role.DOCTOR, phone="+15559991210")
    _register_doctor_push(client, _auth(client, login, doctor))

    class FlakyGateway:
        def send_push(
            self, *, token: str, title: str, body: str, attempt_id: str
        ) -> str:
            raise RuntimeError("fcm down")

        def send_sms(self, *, to: str, body: str) -> str:
            return "sms-ok"

        def place_voice_call(self, *, to: str, twiml_url: str) -> str:
            return "voice-ok"

    monkeypatch.setattr(
        emergency_service, "get_notification_gateway", lambda: FlakyGateway()
    )
    resp = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "flaky",
        },
    )
    assert resp.status_code == 200, resp.text
    by_channel = _by_channel(session)
    assert by_channel[NotificationChannel.FCM.value][0].status == "failed"
    assert by_channel[NotificationChannel.FCM.value][0].error == "RuntimeError"
    assert by_channel[NotificationChannel.SMS.value][0].status == "sent"
    assert by_channel[NotificationChannel.VOICE.value][0].status == "sent"


def test_case_and_queued_attempts_commit_before_any_send(
    client: TestClient, project: Project, session: Session, make_user, login, monkeypatch
) -> None:
    """Durability contract: the case + its queued attempts commit atomically
    in one transaction; only then does any provider call happen."""
    resident_token, _ = _onboard(client, project, session, "+15559990005")
    doctor = make_user(Role.DOCTOR, phone="+15559991116")
    _register_doctor_push(client, _auth(client, login, doctor))

    events: list[str] = []

    class Gateway:
        def send_push(
            self, *, token: str, title: str, body: str, attempt_id: str
        ) -> str:
            events.append("push")
            return "ordered-push"

        def send_sms(self, *, to: str, body: str) -> str:
            return "ordered-sms"

        def place_voice_call(self, *, to: str, twiml_url: str) -> str:
            return "ordered-voice"

    def after_commit(_session) -> None:  # noqa: ANN001
        events.append("commit")

    monkeypatch.setattr(emergency_service, "get_notification_gateway", lambda: Gateway())
    event.listen(OrmSession, "after_commit", after_commit)
    try:
        resp = client.post(
            "/api/v1/emergency/alerts",
            json={"symptom_codes": ["fall"]},
            headers={
                "Authorization": f"Bearer {resident_token}",
                "Idempotency-Key": "ordered",
            },
        )
    finally:
        event.remove(OrmSession, "after_commit", after_commit)

    assert resp.status_code == 200, resp.text
    # The case (+ all queued attempts) is durable before the first send.
    assert events[0] == "commit"
    assert "push" in events
    assert events.index("commit") < events.index("push")
    attempts = session.exec(select(NotificationAttempt)).all()
    assert len(attempts) == 3
    assert {a.status for a in attempts} == {NotificationStatus.SENT.value}


def test_queued_attempts_are_durable_and_resume_on_replay(
    client: TestClient, project: Project, session: Session, make_user, login, monkeypatch
) -> None:
    """A crash after the case commit but before delivery leaves persisted
    queued attempts; the idempotent retry resumes and finishes delivery."""
    resident_token, _ = _onboard(client, project, session, "+15559990030")
    doctor = make_user(Role.DOCTOR, phone="+15559991230")
    _register_doctor_push(client, _auth(client, login, doctor))
    headers = {
        "Authorization": f"Bearer {resident_token}",
        "Idempotency-Key": "resume",
    }
    body = {"symptom_codes": ["fall"]}

    # Simulate a crash between the commit and delivery.
    monkeypatch.setattr(emergency_service, "_deliver_pending", lambda *a, **k: None)
    first = client.post("/api/v1/emergency/alerts", json=body, headers=headers)
    assert first.status_code == 200, first.text
    attempts = session.exec(select(NotificationAttempt)).all()
    assert len(attempts) == 3
    assert {a.status for a in attempts} == {NotificationStatus.QUEUED.value}
    assert len(session.exec(select(EmergencyCase)).all()) == 1

    # Recover: the retried request resumes the outbox and delivers.
    monkeypatch.undo()
    replay = client.post("/api/v1/emergency/alerts", json=body, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    session.expire_all()
    after = _by_channel(session)
    assert all(
        a.status != NotificationStatus.QUEUED.value
        for rows in after.values()
        for a in rows
    )
    assert after[NotificationChannel.SMS.value][0].status == "sent"
    assert after[NotificationChannel.VOICE.value][0].status == "sent"
    assert len(session.exec(select(EmergencyCase)).all()) == 1


def test_on_call_schedule_overrides_first_active_doctor(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990006")
    make_user(Role.DOCTOR, phone="+15559991117")  # earliest active doctor
    on_call_doc = make_user(Role.DOCTOR, phone="+15559991118")
    _schedule(session, project, on_call_doc, Role.DOCTOR, phone="+15550001234")

    resp = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "sched",
        },
    )
    assert resp.status_code == 200, resp.text
    # The scheduled on-call doctor wins over the older "first active doctor".
    assert resp.json()["assigned_doctor_id"] == str(on_call_doc.id)


def test_no_schedule_falls_back_to_first_active_doctor(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990007")
    doctor = make_user(Role.DOCTOR, phone="+15559991119")
    resp = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "fallback-doc",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assigned_doctor_id"] == str(doctor.id)


def test_backup_escalation_after_timeout_is_idempotent(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990008")
    primary = make_user(Role.DOCTOR, phone="+15559991120")
    backup = make_user(Role.DOCTOR, phone="+15559991121")
    _schedule(session, project, primary, Role.DOCTOR, phone="+15550000001")
    _schedule(session, project, backup, Role.DOCTOR, backup=True, phone="+15550000002")
    ops = make_user(Role.OPS, phone="+15559991122")

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "esc",
        },
    )
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    cid = uuid.UUID(case_id)

    # No ack yet, but not stale → nothing escalates.
    run = client.post(
        "/api/v1/emergency/escalations/run", headers=_auth(client, login, ops)
    )
    assert run.status_code == 200, run.text
    assert run.json()["escalated_case_ids"] == []

    # Backdate the alert past the ack timeout.
    case = session.get(EmergencyCase, cid)
    case.alert_time = utcnow() - timedelta(seconds=120)
    session.add(case)
    session.commit()

    run = client.post(
        "/api/v1/emergency/escalations/run", headers=_auth(client, login, ops)
    )
    assert run.status_code == 200, run.text
    assert run.json()["escalated_case_ids"] == [case_id]

    events = [
        e.event_type
        for e in session.exec(
            select(CaseEvent).where(CaseEvent.case_id == cid)
        ).all()
    ]
    assert events.count(emergency_service.BACKUP_ESCALATED_EVENT) == 1
    assert AuditAction.EMERGENCY_ALERT_ESCALATED.value in list(
        session.exec(select(AuditLog.action)).all()
    )
    backup_attempts = [
        a
        for a in session.exec(select(NotificationAttempt)).all()
        if a.recipient_id == backup.id
    ]
    assert backup_attempts, "backup doctor should have been paged"

    # Running again must not double-escalate.
    again = client.post(
        "/api/v1/emergency/escalations/run", headers=_auth(client, login, ops)
    )
    assert again.json()["escalated_case_ids"] == []
    events_after = session.exec(
        select(CaseEvent).where(
            CaseEvent.case_id == cid,
            CaseEvent.event_type == emergency_service.BACKUP_ESCALATED_EVENT,
        )
    ).all()
    assert len(events_after) == 1


def test_escalation_run_requires_ops_role(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    doctor = make_user(Role.DOCTOR, phone="+15559991130")
    resp = client.post(
        "/api/v1/emergency/escalations/run", headers=_auth(client, login, doctor)
    )
    assert resp.status_code == 403


def test_fallback_numbers_resolved_from_backend(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990009")
    doctor = make_user(Role.DOCTOR, phone="+15559991140")
    _schedule(session, project, doctor, Role.DOCTOR, phone="+15550009999")
    desk = make_user(Role.SECURITY_DESK, phone="+15559991141")
    _schedule(session, project, desk, Role.SECURITY_DESK, phone="+15550007777")

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "fb",
        },
    )
    case_id = created.json()["id"]
    resp = client.get(
        f"/api/v1/emergency/alerts/{case_id}/fallback-numbers",
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert resp.status_code == 200, resp.text
    nums = resp.json()
    assert nums["emergency_108"] == "108"
    assert nums["emergency_112"] == "112"
    assert nums["doctor"] == "+15550009999"  # from the on-call schedule
    assert nums["family_primary"] == "+15557770001"  # resident's primary contact
    assert nums["security_desk"] == "+15550007777"  # project opted in

    assert AuditAction.EMERGENCY_FALLBACK_NUMBERS_READ.value in list(
        session.exec(select(AuditLog.action)).all()
    )


def test_fallback_numbers_hide_security_desk_when_project_opted_out(
    client: TestClient, session: Session, make_user
) -> None:
    quiet = Project(name="No Desk Residency", enable_security_desk_alerts=False)
    session.add(quiet)
    session.commit()
    session.refresh(quiet)
    resident_token, _ = _onboard(client, quiet, session, "+15559990020")
    make_user(Role.DOCTOR, phone="+15559991150")

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "fb2",
        },
    )
    resp = client.get(
        f"/api/v1/emergency/alerts/{created.json()['id']}/fallback-numbers",
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["security_desk"] is None


def test_fallback_tap_records_case_event_and_audit(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990011")
    make_user(Role.DOCTOR, phone="+15559991160")
    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "tap",
        },
    )
    case_id = created.json()["id"]

    bad = client.post(
        f"/api/v1/emergency/alerts/{case_id}/fallback",
        json={"channel": "not-a-channel"},
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert bad.status_code == 422

    tap = client.post(
        f"/api/v1/emergency/alerts/{case_id}/fallback",
        json={"channel": FallbackChannel.EMERGENCY_108.value},
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert tap.status_code == 200, tap.text
    assert tap.json()["recorded"] is True

    ev = session.exec(
        select(CaseEvent).where(
            CaseEvent.case_id == uuid.UUID(case_id),
            CaseEvent.event_type == emergency_service.FALLBACK_INVOKED_EVENT,
        )
    ).first()
    assert ev is not None
    assert ev.meta["channel"] == FallbackChannel.EMERGENCY_108.value
    assert AuditAction.EMERGENCY_FALLBACK_INVOKED.value in list(
        session.exec(select(AuditLog.action)).all()
    )


def test_fallback_endpoints_reject_non_owner(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    owner_token, _ = _onboard(client, project, session, "+15559990012")
    make_user(Role.DOCTOR, phone="+15559991170")
    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {owner_token}",
            "Idempotency-Key": "owned",
        },
    )
    case_id = created.json()["id"]

    other_token, _ = _onboard(client, project, session, "+15559990013")
    resp = client.get(
        f"/api/v1/emergency/alerts/{case_id}/fallback-numbers",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert resp.status_code == 404


def test_security_desk_gets_minimal_payload_only_when_opted_in(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    # conftest `project` has enable_security_desk_alerts=True.
    resident_token, _ = _onboard(client, project, session, "+15559990014")
    make_user(Role.DOCTOR, phone="+15559991180")
    desk = make_user(Role.SECURITY_DESK, phone="+15559991181")
    _schedule(session, project, desk, Role.SECURITY_DESK, phone="+15550005555")

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["chest_pain"], "location_text": "Tower B lift"},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "sd",
        },
    )
    assert created.status_code == 200, created.text

    desk_attempts = [
        a
        for a in session.exec(select(NotificationAttempt)).all()
        if a.recipient_id == desk.id
    ]
    assert len(desk_attempts) == 1
    assert desk_attempts[0].channel == NotificationChannel.SMS.value
    assert desk_attempts[0].status == NotificationStatus.SENT.value
    # notification_attempts never persists a body / symptoms / history.
    assert not hasattr(desk_attempts[0], "body")
    assert desk_attempts[0].error is None


def test_delivery_claim_prevents_concurrent_double_send(
    session: Session, project: Project, make_user, monkeypatch
) -> None:
    """A concurrent delivery (e.g. original request + lost-response replay)
    must not both call the provider for the same queued attempt."""
    resident_user = User(
        project_id=project.id,
        phone="+15559992001",
        role=Role.RESIDENT.value,
        full_name="Race Resident",
    )
    session.add(resident_user)
    session.commit()
    resident = Resident(
        user_id=resident_user.id,
        project_id=project.id,
        flat_villa_number="C-1",
        dob=date(1950, 1, 1),
        gender="female",
    )
    session.add(resident)
    doctor = make_user(Role.DOCTOR, phone="+15559992002")
    session.add(
        DeviceToken(
            project_id=project.id,
            user_id=doctor.id,
            platform="web",
            push_token="race-token",
            last_seen_at=utcnow(),
        )
    )
    session.commit()
    case = EmergencyCase(
        project_id=project.id,
        resident_id=resident.id,
        created_by_user_id=resident_user.id,
        assigned_doctor_id=doctor.id,
        status=CaseStatus.ALERTED.value,
        symptom_codes=[],
    )
    session.add(case)
    session.commit()
    session.add(
        NotificationAttempt(
            project_id=project.id,
            case_id=case.id,
            channel=NotificationChannel.FCM.value,
            recipient_id=doctor.id,
            status=NotificationStatus.QUEUED.value,
            attempted_at=utcnow(),
        )
    )
    session.commit()

    sends: list[int] = []

    class Reentrant:
        def send_push(
            self, *, token: str, title: str, body: str, attempt_id: str
        ) -> str:
            sends.append(1)
            # Simulate a concurrent deliverer arriving mid-send.
            emergency_service._deliver_pending(session, case=case)
            return "race-ref"

        def send_sms(self, *, to: str, body: str) -> str:
            return "sms"

        def place_voice_call(self, *, to: str, twiml_url: str) -> str:
            return "voice"

    monkeypatch.setattr(
        emergency_service, "get_notification_gateway", lambda: Reentrant()
    )
    emergency_service._deliver_pending(session, case=case)

    assert sends == [1]  # claimed once; the reentrant pass found nothing queued
    attempts = session.exec(select(NotificationAttempt)).all()
    assert len(attempts) == 1
    assert attempts[0].status == NotificationStatus.SENT.value


def test_case_status_is_owner_scoped_and_reflects_ack(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    owner_token, _ = _onboard(client, project, session, "+15559990040")
    make_user(Role.DOCTOR, phone="+15559991240")
    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {owner_token}",
            "Idempotency-Key": "status",
        },
    )
    case_id = created.json()["id"]

    s = client.get(
        f"/api/v1/emergency/alerts/{case_id}/status",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert s.status_code == 200, s.text
    assert s.json() == {
        "case_id": case_id,
        "status": CaseStatus.ALERTED.value,
        "acknowledged": False,
    }

    # A non-owner resident gets 404 (no existence leak).
    other_token, _ = _onboard(client, project, session, "+15559990041")
    other = client.get(
        f"/api/v1/emergency/alerts/{case_id}/status",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert other.status_code == 404

    # Once acknowledged, the resident sees it (drives the fallback countdown).
    case = session.get(EmergencyCase, uuid.UUID(case_id))
    case.acknowledged_at = utcnow()
    case.status = CaseStatus.ACKNOWLEDGED.value
    session.add(case)
    session.commit()
    s2 = client.get(
        f"/api/v1/emergency/alerts/{case_id}/status",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert s2.json()["acknowledged"] is True


def test_case_lifecycle_sets_timestamps_events_audit_and_stops_escalation(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990060")
    doctor = make_user(Role.DOCTOR, phone="+15559991260")
    ops = make_user(Role.OPS, phone="+15559991261")
    doctor_headers = _auth(client, login, doctor)

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "life",
        },
    )
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    cid = uuid.UUID(case_id)

    case = session.get(EmergencyCase, cid)
    case.alert_time = utcnow() - timedelta(seconds=120)
    session.add(case)
    session.commit()

    ack = client.post(
        f"/api/v1/emergency/alerts/{case_id}/transition",
        json={"target_status": CaseStatus.ACKNOWLEDGED.value},
        headers=doctor_headers,
    )
    assert ack.status_code == 200, ack.text
    assert ack.json()["status"] == CaseStatus.ACKNOWLEDGED.value
    assert ack.json()["acknowledged_at"] is not None

    # acknowledged_at is load-bearing for Slice 6 escalation and mobile status.
    run = client.post(
        "/api/v1/emergency/escalations/run", headers=_auth(client, login, ops)
    )
    assert run.status_code == 200
    assert run.json()["escalated_case_ids"] == []
    owner_status = client.get(
        f"/api/v1/emergency/alerts/{case_id}/status",
        headers={"Authorization": f"Bearer {resident_token}"},
    )
    assert owner_status.json()["acknowledged"] is True

    for target in (
        CaseStatus.EN_ROUTE,
        CaseStatus.ON_SITE,
        CaseStatus.TREATED_ON_SITE,
    ):
        resp = client.post(
            f"/api/v1/emergency/alerts/{case_id}/transition",
            json={
                "target_status": target.value,
                "resolved_outcome": (
                    "Treated on site" if target is CaseStatus.TREATED_ON_SITE else None
                ),
            },
            headers=doctor_headers,
        )
        assert resp.status_code == 200, resp.text
    closed = client.post(
        f"/api/v1/emergency/alerts/{case_id}/transition",
        json={"target_status": CaseStatus.CLOSED.value},
        headers=doctor_headers,
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == CaseStatus.CLOSED.value
    assert closed.json()["on_site_at"] is not None
    assert closed.json()["closed_at"] is not None
    assert closed.json()["resolved_outcome"] == "Treated on site"

    feed = client.get("/api/v1/emergency/alerts/active", headers=doctor_headers)
    assert feed.status_code == 200
    assert feed.json() == []

    event_types = [
        e.event_type
        for e in session.exec(select(CaseEvent).where(CaseEvent.case_id == cid)).all()
    ]
    assert "case_acknowledged" in event_types
    assert "case_en_route" in event_types
    assert "case_on_site" in event_types
    assert "case_treated_on_site" in event_types
    assert "case_closed" in event_types
    assert emergency_service.BACKUP_ESCALATED_EVENT not in event_types
    assert AuditAction.EMERGENCY_CASE_TRANSITIONED.value in list(
        session.exec(select(AuditLog.action)).all()
    )


def test_lifecycle_rejects_invalid_order_and_restricts_outcomes(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990061")
    doctor = make_user(Role.DOCTOR, phone="+15559991262")
    nurse = make_user(Role.NURSE, phone="+15559991263")
    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "life-invalid",
        },
    )
    case_id = created.json()["id"]

    bad_order = client.post(
        f"/api/v1/emergency/alerts/{case_id}/transition",
        json={"target_status": CaseStatus.ON_SITE.value},
        headers=_auth(client, login, doctor),
    )
    assert bad_order.status_code == 409
    assert bad_order.json()["error"]["code"] == "invalid_case_transition"

    doctor_headers = _auth(client, login, doctor)
    for target in (CaseStatus.ACKNOWLEDGED, CaseStatus.EN_ROUTE, CaseStatus.ON_SITE):
        ok = client.post(
            f"/api/v1/emergency/alerts/{case_id}/transition",
            json={"target_status": target.value},
            headers=doctor_headers,
        )
        assert ok.status_code == 200, ok.text

    nurse_outcome = client.post(
        f"/api/v1/emergency/alerts/{case_id}/transition",
        json={"target_status": CaseStatus.ESCALATED.value},
        headers=_auth(client, login, nurse),
    )
    assert nurse_outcome.status_code == 403
    assert nurse_outcome.json()["error"]["code"] == "doctor_required"

    other_project = Project(name="Lifecycle Other")
    session.add(other_project)
    session.commit()
    outsider = User(
        project_id=other_project.id,
        phone="+15559991264",
        role=Role.DOCTOR.value,
        full_name="Outside Doctor",
    )
    session.add(outsider)
    session.commit()
    cross = client.get(
        f"/api/v1/emergency/alerts/{case_id}",
        headers=_auth(client, login, outsider),
    )
    assert cross.status_code == 404


def test_vitals_and_telemedicine_notes_are_audited_and_visible_in_detail(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990062")
    doctor = make_user(Role.DOCTOR, phone="+15559991265")
    nurse = make_user(Role.NURSE, phone="+15559991266")
    doctor_headers = _auth(client, login, doctor)
    nurse_headers = _auth(client, login, nurse)

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "life-vitals",
        },
    )
    case_id = created.json()["id"]
    for target in (CaseStatus.ACKNOWLEDGED, CaseStatus.EN_ROUTE, CaseStatus.ON_SITE):
        resp = client.post(
            f"/api/v1/emergency/alerts/{case_id}/transition",
            json={"target_status": target.value},
            headers=doctor_headers,
        )
        assert resp.status_code == 200, resp.text

    too_early = client.post(
        f"/api/v1/emergency/alerts/{case_id}/vitals",
        json={},
        headers=nurse_headers,
    )
    assert too_early.status_code == 422

    vitals = client.post(
        f"/api/v1/emergency/alerts/{case_id}/vitals",
        json={
            "blood_pressure_systolic": 128,
            "blood_pressure_diastolic": 82,
            "spo2_percent": 97,
            "heart_rate_bpm": 88,
            "respiratory_rate_bpm": 18,
            "temperature_c": 37.1,
        },
        headers=nurse_headers,
    )
    assert vitals.status_code == 200, vitals.text
    assert vitals.json()["spo2_percent"] == 97

    missing_fields = client.post(
        f"/api/v1/emergency/alerts/{case_id}/notes",
        json={"note_type": "treatment", "body": "Care provided."},
        headers=doctor_headers,
    )
    assert missing_fields.status_code == 422
    assert missing_fields.json()["error"]["code"] == "telemedicine_fields_required"

    note = client.post(
        f"/api/v1/emergency/alerts/{case_id}/notes",
        json={
            "note_type": "treatment",
            "body": "Resident monitored and supported on site.",
            "doctor_name": "Dr Kavita Rao",
            "doctor_registration_number": "TSMC-12345",
            "consultation_timestamp": utcnow().isoformat(),
            "advice_given": "Continue observation and call hospital if symptoms worsen.",
            "patient_consent_obtained": True,
        },
        headers=doctor_headers,
    )
    assert note.status_code == 200, note.text
    assert note.json()["doctor_registration_number"] == "TSMC-12345"
    assert note.json()["patient_consent_obtained"] is True

    detail = client.get(
        f"/api/v1/emergency/alerts/{case_id}",
        headers=doctor_headers,
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["vitals"][0]["heart_rate_bpm"] == 88
    assert body["notes"][0]["advice_given"].startswith("Continue observation")
    assert any(e["event_type"] == emergency_service.VITALS_RECORDED_EVENT for e in body["events"])
    assert any(
        e["event_type"] == emergency_service.CASE_NOTE_RECORDED_EVENT
        for e in body["events"]
    )

    actions = list(session.exec(select(AuditLog.action)).all())
    assert AuditAction.CASE_VITAL_RECORDED.value in actions
    assert AuditAction.CASE_NOTE_RECORDED.value in actions
    assert AuditAction.EMERGENCY_CASE_READ.value in actions


def test_emergency_kpis_are_aggregate_and_builder_visible(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990063")
    doctor = make_user(Role.DOCTOR, phone="+15559991267")
    builder = make_user(Role.BUILDER_ADMIN, phone="+15559991268")
    doctor_headers = _auth(client, login, doctor)

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "life-kpi",
        },
    )
    case_id = created.json()["id"]
    case = session.get(EmergencyCase, uuid.UUID(case_id))
    case.alert_time = utcnow() - timedelta(seconds=300)
    session.add(case)
    session.commit()

    for target in (
        CaseStatus.ACKNOWLEDGED,
        CaseStatus.EN_ROUTE,
        CaseStatus.ON_SITE,
        CaseStatus.TREATED_ON_SITE,
        CaseStatus.CLOSED,
    ):
        resp = client.post(
            f"/api/v1/emergency/alerts/{case_id}/transition",
            json={"target_status": target.value},
            headers=doctor_headers,
        )
        assert resp.status_code == 200, resp.text

    kpis = client.get("/api/v1/emergency/kpis", headers=_auth(client, login, builder))
    assert kpis.status_code == 200, kpis.text
    assert kpis.json()["total_cases"] == 1
    assert kpis.json()["closed_cases"] == 1
    assert kpis.json()["average_ack_seconds"] is not None
    assert "resident_name" not in kpis.text
    assert AuditAction.EMERGENCY_KPI_READ.value in list(
        session.exec(select(AuditLog.action)).all()
    )


def test_oncall_resolution_ignores_wrong_project_or_role(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    """Bad/stale schedule data must never page the wrong tenant or role."""
    resident_token, _ = _onboard(client, project, session, "+15559990050")
    local_doctor = make_user(Role.DOCTOR, phone="+15559991250")

    # A doctor who belongs to a different project.
    other_project = Project(name="Other Residency")
    session.add(other_project)
    session.commit()
    session.refresh(other_project)
    foreign_doctor = User(
        project_id=other_project.id,
        phone="+15559991251",
        role=Role.DOCTOR.value,
        full_name="Foreign Doctor",
    )
    # A user in this project but with the wrong role.
    nurse = make_user(Role.NURSE, phone="+15559991252")
    session.add(foreign_doctor)
    session.commit()

    # Both schedule rows are invalid: cross-tenant user, and wrong-role user.
    session.add(
        OnCallSchedule(
            project_id=project.id,
            role=Role.DOCTOR.value,
            user_id=foreign_doctor.id,
            starts_at=utcnow() - timedelta(seconds=60),
            ends_at=utcnow() + timedelta(hours=1),
            is_backup=False,
            contact_phone="+15550000000",
        )
    )
    session.add(
        OnCallSchedule(
            project_id=project.id,
            role=Role.DOCTOR.value,
            user_id=nurse.id,
            starts_at=utcnow() - timedelta(seconds=60),
            ends_at=utcnow() + timedelta(hours=1),
            is_backup=False,
        )
    )
    session.commit()

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "bad-sched",
        },
    )
    assert created.status_code == 200, created.text
    # Falls back to the only eligible doctor in this project.
    assert created.json()["assigned_doctor_id"] == str(local_doctor.id)


def test_security_desk_not_paged_when_no_oncall(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559990015")
    make_user(Role.DOCTOR, phone="+15559991190")
    make_user(Role.SECURITY_DESK, phone="+15559991191")  # no schedule row

    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "sd-none",
        },
    )
    assert created.status_code == 200
    desk_user = session.exec(
        select(User).where(User.phone == "+15559991191")
    ).first()
    desk_attempts = [
        a
        for a in session.exec(select(NotificationAttempt)).all()
        if a.recipient_id == desk_user.id
    ]
    assert desk_attempts == []


def test_fallback_tap_idempotency_dedupes_offline_replay(
    client: TestClient, project: Project, session: Session, make_user
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559993001")
    make_user(Role.DOCTOR, phone="+15559993002")
    created = client.post(
        "/api/v1/emergency/alerts",
        json={"symptom_codes": ["fall"]},
        headers={
            "Authorization": f"Bearer {resident_token}",
            "Idempotency-Key": "fb-replay-alert",
        },
    )
    assert created.status_code == 200, created.text
    case_id = created.json()["id"]
    headers = {
        "Authorization": f"Bearer {resident_token}",
        "Idempotency-Key": "fb-tap-offline-1",
    }

    first = client.post(
        f"/api/v1/emergency/alerts/{case_id}/fallback",
        json={"channel": FallbackChannel.EMERGENCY_108.value},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    replay = client.post(
        f"/api/v1/emergency/alerts/{case_id}/fallback",
        json={"channel": FallbackChannel.EMERGENCY_108.value},
        headers=headers,
    )
    assert replay.status_code == 200, replay.text

    cid = uuid.UUID(case_id)
    events = session.exec(
        select(CaseEvent).where(
            CaseEvent.case_id == cid,
            CaseEvent.event_type == emergency_service.FALLBACK_INVOKED_EVENT,
        )
    ).all()
    assert len(events) == 1
    assert events[0].meta == {"channel": FallbackChannel.EMERGENCY_108.value}
    assert (
        list(session.exec(select(AuditLog.action)).all()).count(
            AuditAction.EMERGENCY_FALLBACK_INVOKED.value
        )
        == 1
    )

    changed = client.post(
        f"/api/v1/emergency/alerts/{case_id}/fallback",
        json={"channel": FallbackChannel.EMERGENCY_112.value},
        headers=headers,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_key_conflict"


def test_stuck_notification_reaper_requeues_and_delivers(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    _, resident = _onboard(client, project, session, "+15559993003")
    doctor = make_user(Role.DOCTOR, phone="+15559993004")
    _register_doctor_push(client, _auth(client, login, doctor))
    ops = make_user(Role.OPS, phone="+15559993005")

    case = EmergencyCase(
        project_id=project.id,
        resident_id=resident.id,
        created_by_user_id=resident.user_id,
        assigned_doctor_id=doctor.id,
        status=CaseStatus.ALERTED.value,
        symptom_codes=[],
    )
    session.add(case)
    session.commit()
    stuck = NotificationAttempt(
        project_id=project.id,
        case_id=case.id,
        channel=NotificationChannel.FCM.value,
        recipient_id=doctor.id,
        status=NotificationStatus.SENDING.value,
        error="process_crashed_mid_send",
        attempted_at=utcnow() - timedelta(seconds=600),
    )
    fresh = NotificationAttempt(
        project_id=project.id,
        case_id=case.id,
        channel=NotificationChannel.SMS.value,
        recipient_id=doctor.id,
        status=NotificationStatus.SENDING.value,
        error="active_provider_call",
        attempted_at=utcnow(),
    )
    session.add(stuck)
    session.add(fresh)
    session.commit()

    resp = client.post(
        "/api/v1/emergency/notifications/requeue-stuck?older_than_seconds=300",
        headers=_auth(client, login, ops),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["requeued_attempt_ids"] == [str(stuck.id)]

    session.expire_all()
    recovered = session.get(NotificationAttempt, stuck.id)
    still_active = session.get(NotificationAttempt, fresh.id)
    assert recovered.status == NotificationStatus.SENT.value
    assert recovered.provider_ref == "stub-push"
    assert recovered.error is None
    assert still_active.status == NotificationStatus.SENDING.value
    assert still_active.error == "active_provider_call"
    assert AuditAction.EMERGENCY_NOTIFICATION_REQUEUED.value in list(
        session.exec(select(AuditLog.action)).all()
    )


def test_emergency_alert_load_smoke_dedupes_replays_and_attempts(
    client: TestClient, project: Project, session: Session, make_user, login
) -> None:
    resident_token, _ = _onboard(client, project, session, "+15559993006")
    doctor = make_user(Role.DOCTOR, phone="+15559993007")
    _register_doctor_push(client, _auth(client, login, doctor))
    payload = {"symptom_codes": ["fall"], "location_text": "Tower A lobby"}

    created_ids: list[str] = []
    for i in range(30):
        resp = client.post(
            "/api/v1/emergency/alerts",
            json=payload,
            headers={
                "Authorization": f"Bearer {resident_token}",
                "Idempotency-Key": f"load-{i}",
            },
        )
        assert resp.status_code == 200, resp.text
        created_ids.append(resp.json()["id"])

    for i in range(5):
        replay = client.post(
            "/api/v1/emergency/alerts",
            json=payload,
            headers={
                "Authorization": f"Bearer {resident_token}",
                "Idempotency-Key": f"load-{i}",
            },
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == created_ids[i]

    cases = session.exec(select(EmergencyCase)).all()
    attempts = session.exec(select(NotificationAttempt)).all()
    assert len(cases) == 30
    assert len(set(created_ids)) == 30
    assert len(attempts) == 30 * 3
    assert {a.status for a in attempts} == {NotificationStatus.SENT.value}
