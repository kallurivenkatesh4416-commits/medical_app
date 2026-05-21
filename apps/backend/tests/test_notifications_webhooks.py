"""Slice 16 — Twilio status webhook + FCM device-ack endpoint tests.

The webhooks are the loop that closes Slice 6's `notification_attempts`
trail: Twilio posts a `delivered`/`failed` callback when SMS or voice
reaches a terminal state; the resident's device POSTs an FCM ack when
push lands on the phone. Both paths must:

  - update `notification_attempts.status` to the right NotificationStatus,
  - write an audit row that captures the from/to transition (so the
    audit log carries the delivery history without leaking PHI),
  - be idempotent — Twilio retries on non-2xx forever; a second device
    ack is a no-op,
  - reject unauthorized writers (Twilio signature, FCM owner check).
"""

import base64
import hashlib
import hmac
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.enums import (
    AuditAction,
    NotificationChannel,
    NotificationStatus,
    Role,
)
from app.models.audit import AuditLog
from app.models.emergency import EmergencyCase, NotificationAttempt
from app.models.project import Project
from app.models.resident import Resident
from app.models.user import User

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_attempt(
    session: Session,
    *,
    project: Project,
    resident: Resident,
    recipient: User,
    channel: NotificationChannel,
    provider_ref: str,
    status: NotificationStatus = NotificationStatus.SENT,
) -> NotificationAttempt:
    case = EmergencyCase(
        project_id=project.id,
        resident_id=resident.id,
        created_by_user_id=resident.user_id,
        status="alerted",
        symptom_codes=[],
    )
    session.add(case)
    session.flush()
    attempt = NotificationAttempt(
        project_id=project.id,
        case_id=case.id,
        channel=channel.value,
        recipient_id=recipient.id,
        status=status.value,
        provider_ref=provider_ref,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def _twilio_signature(*, auth_token: str, url: str, form: dict[str, str]) -> str:
    sorted_params = "".join(f"{k}{form[k]}" for k in sorted(form))
    digest = hmac.new(
        auth_token.encode("utf-8"),
        f"{url}{sorted_params}".encode(),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _resident_with_user(
    session: Session, project: Project, make_user
) -> tuple[Resident, User]:
    user = make_user(Role.RESIDENT)
    resident = Resident(
        user_id=user.id,
        project_id=project.id,
        flat_villa_number="A-1",
        dob=date(1960, 1, 1),
        gender="prefer_not_to_say",
    )
    session.add(resident)
    session.commit()
    session.refresh(resident)
    return resident, user


# --------------------------------------------------------------------------- #
# Twilio status webhook                                                       #
# --------------------------------------------------------------------------- #


def test_twilio_sms_delivered_marks_attempt_delivered_and_audits(
    monkeypatch, client: TestClient, session: Session, project: Project, make_user
):
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.SMS,
        provider_ref="SMtest1",
    )

    from app.services import notifications as notifications_mod

    monkeypatch.setattr(
        notifications_mod.get_settings.__wrapped__
        if hasattr(notifications_mod.get_settings, "__wrapped__")
        else notifications_mod.get_settings,
        "__call__",
        lambda *a, **kw: None,
        raising=False,
    )
    # Override the API settings reader so the webhook validates against a
    # known auth token.
    from app.api import notifications as notifications_api

    class _Settings:
        twilio_account_sid = "ACtest"
        twilio_auth_token = "secret-token"

    monkeypatch.setattr(notifications_api, "get_settings", lambda: _Settings())

    url = "http://testserver/api/v1/notifications/twilio/status"
    form = {"MessageSid": "SMtest1", "MessageStatus": "delivered"}
    sig = _twilio_signature(auth_token="secret-token", url=url, form=form)

    resp = client.post(
        "/api/v1/notifications/twilio/status",
        data=form,
        headers={"X-Twilio-Signature": sig},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"accepted": True, "matched": True}

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.DELIVERED.value

    audit = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.EMERGENCY_NOTIFICATION_STATUS_UPDATED.value
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].meta["from_status"] == NotificationStatus.SENT.value
    assert audit[0].meta["to_status"] == NotificationStatus.DELIVERED.value
    assert audit[0].meta["channel"] == NotificationChannel.SMS.value
    # Provider ref is opaque, not PHI — recording it lets ops trace
    # delivery without exposing patient data.
    assert audit[0].meta["provider_ref"] == "SMtest1"


def test_twilio_voice_failed_with_error_message_stored(
    monkeypatch, client: TestClient, session: Session, project: Project, make_user
):
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.VOICE,
        provider_ref="CAtest1",
    )

    from app.api import notifications as notifications_api

    class _Settings:
        twilio_account_sid = "ACtest"
        twilio_auth_token = "secret-token"

    monkeypatch.setattr(notifications_api, "get_settings", lambda: _Settings())

    url = "http://testserver/api/v1/notifications/twilio/status"
    form = {
        "CallSid": "CAtest1",
        "CallStatus": "failed",
        "ErrorMessage": "no-answer-30s",
    }
    sig = _twilio_signature(auth_token="secret-token", url=url, form=form)

    resp = client.post(
        "/api/v1/notifications/twilio/status",
        data=form,
        headers={"X-Twilio-Signature": sig},
    )
    assert resp.status_code == 200
    assert resp.json()["matched"] is True

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.FAILED.value
    assert attempt.error == "no-answer-30s"


def test_twilio_invalid_signature_returns_403_and_does_not_update(
    monkeypatch, client: TestClient, session: Session, project: Project, make_user
):
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.SMS,
        provider_ref="SMtest2",
    )

    from app.api import notifications as notifications_api

    class _Settings:
        twilio_account_sid = "ACtest"
        twilio_auth_token = "secret-token"

    monkeypatch.setattr(notifications_api, "get_settings", lambda: _Settings())

    resp = client.post(
        "/api/v1/notifications/twilio/status",
        data={"MessageSid": "SMtest2", "MessageStatus": "delivered"},
        headers={"X-Twilio-Signature": "wrong-sig"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "invalid_twilio_signature"

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.SENT.value, (
        "rejected signature must not move the attempt forward"
    )


def test_twilio_intermediate_status_is_no_op_but_200(
    monkeypatch, client: TestClient, session: Session, project: Project, make_user
):
    """Twilio sends `queued`/`sending`/`sent` as well as terminal states.
    The webhook accepts them (200, no retry storm) but does not write
    `notification_attempts` rows or audit entries."""
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.SMS,
        provider_ref="SMtest3",
    )

    from app.api import notifications as notifications_api

    class _Settings:
        twilio_account_sid = "ACtest"
        twilio_auth_token = "secret-token"

    monkeypatch.setattr(notifications_api, "get_settings", lambda: _Settings())

    url = "http://testserver/api/v1/notifications/twilio/status"
    form = {"MessageSid": "SMtest3", "MessageStatus": "sending"}
    sig = _twilio_signature(auth_token="secret-token", url=url, form=form)
    resp = client.post(
        "/api/v1/notifications/twilio/status",
        data=form,
        headers={"X-Twilio-Signature": sig},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"accepted": True, "matched": False}

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.SENT.value

    audits = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.EMERGENCY_NOTIFICATION_STATUS_UPDATED.value
        )
    ).all()
    assert audits == []


def test_twilio_unknown_provider_ref_returns_200_matched_false(
    monkeypatch, client: TestClient, session: Session, project: Project, make_user
):
    """A misconfigured callback URL or a re-issued sid that we never saw
    must NOT cause Twilio to retry forever — return 200 with matched=False
    so the issue is visible via ops dashboards, not log storms."""
    from app.api import notifications as notifications_api

    class _Settings:
        twilio_account_sid = "ACtest"
        twilio_auth_token = "secret-token"

    monkeypatch.setattr(notifications_api, "get_settings", lambda: _Settings())

    url = "http://testserver/api/v1/notifications/twilio/status"
    form = {"MessageSid": "SMunknown", "MessageStatus": "delivered"}
    sig = _twilio_signature(auth_token="secret-token", url=url, form=form)

    resp = client.post(
        "/api/v1/notifications/twilio/status",
        data=form,
        headers={"X-Twilio-Signature": sig},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"accepted": True, "matched": False}


def test_twilio_replay_of_same_event_writes_only_one_audit_row(
    monkeypatch, client: TestClient, session: Session, project: Project, make_user
):
    """Idempotency: Twilio retries `delivered` twice -> single audit row."""
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.SMS,
        provider_ref="SMtest4",
    )

    from app.api import notifications as notifications_api

    class _Settings:
        twilio_account_sid = "ACtest"
        twilio_auth_token = "secret-token"

    monkeypatch.setattr(notifications_api, "get_settings", lambda: _Settings())

    url = "http://testserver/api/v1/notifications/twilio/status"
    form = {"MessageSid": "SMtest4", "MessageStatus": "delivered"}
    sig = _twilio_signature(auth_token="secret-token", url=url, form=form)

    for _ in range(3):
        resp = client.post(
            "/api/v1/notifications/twilio/status",
            data=form,
            headers={"X-Twilio-Signature": sig},
        )
        assert resp.status_code == 200

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.DELIVERED.value

    audits = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.EMERGENCY_NOTIFICATION_STATUS_UPDATED.value
        )
    ).all()
    assert len(audits) == 1, "idempotent webhook must not duplicate the audit row"


def test_twilio_keys_not_configured_returns_200_matched_false(
    monkeypatch, client: TestClient
):
    """When the operator has not configured live Twilio keys, the
    webhook accepts-and-drops so a stub backend never produces 5xx
    retry storms."""
    from app.api import notifications as notifications_api

    class _Settings:
        twilio_account_sid = None
        twilio_auth_token = None

    monkeypatch.setattr(notifications_api, "get_settings", lambda: _Settings())

    resp = client.post(
        "/api/v1/notifications/twilio/status",
        data={"MessageSid": "SMx", "MessageStatus": "delivered"},
        headers={"X-Twilio-Signature": "any"},
    )
    assert resp.status_code == 200
    assert resp.json()["matched"] is False


# --------------------------------------------------------------------------- #
# FCM device-ack endpoint                                                     #
# --------------------------------------------------------------------------- #
#
# Slice 16 review #1: the FCM ack endpoint corresponds to the on-call
# doctor / nurse / ops / family receiving the push (Slice 6 §7
# fan-out). The resident is NOT a push recipient in the current flow,
# so these tests target a **doctor**-recipient attempt. The ack body
# carries `attempt_id` — the device reads it out of the FCM `data`
# payload at delivery time (see test_fcm.py for the data-shape proof).


def test_fcm_ack_marks_doctor_attempt_delivered(
    client: TestClient, session: Session, project: Project, make_user, login
):
    """Doctor receives the emergency push; their device POSTs the
    attempt_id back. Status transitions to delivered + one audit row
    captures the from/to transition + provider_ref."""
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.FCM,
        provider_ref="projects/demo/messages/abc",
    )

    headers = {
        "Authorization": f"Bearer {login(doctor.phone)['access_token']}",
    }
    resp = client.post(
        "/api/v1/notifications/fcm/ack",
        json={"attempt_id": str(attempt.id)},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == NotificationStatus.DELIVERED.value
    assert body["channel"] == NotificationChannel.FCM.value

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.DELIVERED.value

    audits = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.EMERGENCY_NOTIFICATION_ACK_RECEIVED.value
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].actor_user_id == doctor.id
    # Audit row carries the FCM `name` (provider_ref) for cross-reference
    # with the FCM console — `attempt_id` is the resource_id field.
    assert audits[0].resource_id == str(attempt.id)
    assert audits[0].meta["provider_ref"] == "projects/demo/messages/abc"


def test_resident_cannot_ack_doctor_attempt(
    client: TestClient, session: Session, project: Project, make_user, login
):
    """The endpoint is open to any authenticated user, but the owner
    check (`attempt.recipient_id == actor.id`) is the security
    boundary: a resident posting an ack for a doctor's FCM attempt
    gets 404 with no existence leak."""
    resident, resident_user = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.FCM,
        provider_ref="projects/demo/messages/xyz",
    )

    headers = {
        "Authorization": f"Bearer {login(resident_user.phone)['access_token']}",
    }
    resp = client.post(
        "/api/v1/notifications/fcm/ack",
        json={"attempt_id": str(attempt.id)},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "notification_attempt_not_found"

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.SENT.value, (
        "non-recipient ack must not mutate the row"
    )


def test_fcm_ack_from_unrelated_doctor_returns_404(
    client: TestClient, session: Session, project: Project, make_user, login
):
    """Even a doctor who is the *wrong* doctor (not the original FCM
    recipient) gets 404 — role alone is not enough; the recipient match
    on the attempt is what authorises the ack."""
    resident, _ = _resident_with_user(session, project, make_user)
    target_doctor = make_user(Role.DOCTOR)
    other_doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=target_doctor,
        channel=NotificationChannel.FCM,
        provider_ref="projects/demo/messages/other",
    )

    headers = {
        "Authorization": f"Bearer {login(other_doctor.phone)['access_token']}",
    }
    resp = client.post(
        "/api/v1/notifications/fcm/ack",
        json={"attempt_id": str(attempt.id)},
        headers=headers,
    )
    assert resp.status_code == 404

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.SENT.value


def test_fcm_ack_is_idempotent(
    client: TestClient, session: Session, project: Project, make_user, login
):
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.FCM,
        provider_ref="projects/demo/messages/idem",
    )

    headers = {
        "Authorization": f"Bearer {login(doctor.phone)['access_token']}",
    }
    for _ in range(3):
        resp = client.post(
            "/api/v1/notifications/fcm/ack",
            json={"attempt_id": str(attempt.id)},
            headers=headers,
        )
        assert resp.status_code == 200

    session.refresh(attempt)
    assert attempt.status == NotificationStatus.DELIVERED.value

    audits = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.EMERGENCY_NOTIFICATION_ACK_RECEIVED.value
        )
    ).all()
    assert len(audits) == 1, "idempotent ack must not duplicate audit"


def test_fcm_ack_for_unknown_attempt_id_returns_404(
    client: TestClient, session: Session, project: Project, make_user, login
):
    _, resident_user = _resident_with_user(session, project, make_user)
    headers = {
        "Authorization": f"Bearer {login(resident_user.phone)['access_token']}",
    }
    # A well-formed UUID that no notification_attempts row has.
    resp = client.post(
        "/api/v1/notifications/fcm/ack",
        json={"attempt_id": "00000000-0000-4000-8000-000000000000"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_fcm_ack_ignores_attempts_on_other_channels(
    client: TestClient, session: Session, project: Project, make_user, login
):
    """SMS / voice attempts share the same `notification_attempts`
    table but have their own delivery callback (Twilio status). The
    FCM ack endpoint must NOT update an SMS/voice row even if the
    actor is the recipient — channel-mismatched lookups return 404."""
    resident, _ = _resident_with_user(session, project, make_user)
    doctor = make_user(Role.DOCTOR)
    sms_attempt = _make_attempt(
        session,
        project=project,
        resident=resident,
        recipient=doctor,
        channel=NotificationChannel.SMS,
        provider_ref="SMabc",
    )

    headers = {
        "Authorization": f"Bearer {login(doctor.phone)['access_token']}",
    }
    resp = client.post(
        "/api/v1/notifications/fcm/ack",
        json={"attempt_id": str(sms_attempt.id)},
        headers=headers,
    )
    assert resp.status_code == 404

    session.refresh(sms_attempt)
    assert sms_attempt.status == NotificationStatus.SENT.value


def test_resident_can_register_own_device_token(
    client: TestClient, session: Session, project: Project, make_user, login
):
    """Slice 16 `/me/device-tokens` endpoint — counterpart to the staff
    `/devices/push-token`. Audit row records platform but never the token."""
    _, resident_user = _resident_with_user(session, project, make_user)
    headers = {
        "Authorization": f"Bearer {login(resident_user.phone)['access_token']}",
    }
    resp = client.post(
        "/api/v1/me/device-tokens",
        json={"token": "resident-fcm-tok-1", "platform": "android"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == {"registered": True}

    audits = session.exec(
        select(AuditLog).where(
            AuditLog.action == AuditAction.DEVICE_TOKEN_REGISTERED.value
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].actor_user_id == resident_user.id
    assert audits[0].meta == {"platform": "android"}
    # Never log the token itself — DPDP discipline holds for device IDs too.
    assert "token" not in audits[0].meta
    assert "resident-fcm-tok-1" not in str(audits[0].meta)


def test_device_token_registration_is_idempotent_per_token(
    client: TestClient, session: Session, project: Project, make_user, login
):
    """Re-registering the same token from the same user upserts (not
    409). One DeviceToken row, two audit rows (one per registration call)."""
    from app.models.emergency import DeviceToken

    _, resident_user = _resident_with_user(session, project, make_user)
    headers = {
        "Authorization": f"Bearer {login(resident_user.phone)['access_token']}",
    }
    for _ in range(2):
        resp = client.post(
            "/api/v1/me/device-tokens",
            json={"token": "same-token", "platform": "android"},
            headers=headers,
        )
        assert resp.status_code == 200

    rows = session.exec(
        select(DeviceToken).where(DeviceToken.push_token == "same-token")
    ).all()
    assert len(rows) == 1
    assert rows[0].user_id == resident_user.id


def test_staff_endpoint_still_rejects_residents(
    client: TestClient, session: Session, project: Project, make_user, login
):
    """The pre-Slice-16 `/devices/push-token` stays staff-only; opening
    it to residents would widen the RBAC contract. The new resident
    endpoint is `/me/device-tokens` (covered above)."""
    _, resident_user = _resident_with_user(session, project, make_user)
    headers = {
        "Authorization": f"Bearer {login(resident_user.phone)['access_token']}",
    }
    resp = client.post(
        "/api/v1/devices/push-token",
        json={"token": "x" * 16, "platform": "android"},
        headers=headers,
    )
    assert resp.status_code == 403
