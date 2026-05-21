"""Slice 16 — notification delivery webhooks + acknowledgments.

Two endpoints:

- ``POST /api/v1/notifications/twilio/status``
    Twilio status callback for SMS + voice. **Unauthenticated by design**
    but ``X-Twilio-Signature`` is validated against the configured auth
    token. Updates ``notification_attempts.status`` to delivered/failed
    based on the provider's terminal event. Idempotent: a retry of the
    same event is a no-op (no second audit row).

- ``POST /api/v1/notifications/fcm/ack``
    Receiving-device FCM delivery ack. Mobile push handler POSTs the
    ``attempt_id`` it read out of the FCM ``data`` payload (Slice 16
    review #1 — the device cannot echo a ``provider_ref`` because FCM
    only returns it after the send completes; the attempt id is the
    pre-existing notification_attempts.id known at send time).

    Open to any authenticated user — the owner check
    (``attempt.recipient_id == actor.id``) is the actual security
    boundary, since Slice 6's fan-out targets the on-call doctor /
    nurse / ops / family for the FCM channel, not the resident.
    Non-owner / unknown id returns 404 with no existence leak (same
    pattern as the resident-owned case-status read from Slice 6
    review).

PHI: neither endpoint touches symptoms, vitals, notes, or records.
The only data they update is `notification_attempts.status` + an
optional bounded error string. Slice 6 PHI invariants hold.
"""

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlmodel import Session

from app.config import get_settings
from app.enums import NotificationChannel, NotificationStatus
from app.models.user import User
from app.security.deps import client_ip, get_current_user, get_db
from app.services import emergency_service
from app.services.auth_service import AuthError
from app.services.twilio_webhook import (
    map_twilio_status,
    verify_twilio_signature,
)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class FcmAckIn(BaseModel):
    # The pre-existing notification_attempts.id (UUID) the device read
    # from the FCM data payload. Slice 16 review #1: this replaces the
    # original `provider_ref` field — see module docstring.
    attempt_id: uuid.UUID


class FcmAckOut(BaseModel):
    status: str
    case_id: str
    channel: str = NotificationChannel.FCM.value


class WebhookStatusOut(BaseModel):
    accepted: bool = True
    # When the provider_ref is unknown we still return 200 (Twilio retries
    # on non-2xx forever, which would flood the log). `matched: false` is
    # the diagnostic signal — the audit log records nothing in that case.
    matched: bool


@router.post("/twilio/status", response_model=WebhookStatusOut)
async def twilio_status_callback(
    request: Request,
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
    session: Session = Depends(get_db),
) -> WebhookStatusOut:
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token):
        # Live keys are not configured — accept-and-drop so a dev who
        # accidentally configures the callback URL on a stub backend gets
        # 200s (not 5xx retry storms).
        return WebhookStatusOut(accepted=True, matched=False)

    raw_form = await request.form()
    form: dict[str, str] = {
        k: v for k, v in raw_form.items() if isinstance(v, str)
    }
    # Twilio signs the EXACT URL it called — include scheme/host/path and
    # any query string. `request.url` gives us that as a `URL` object.
    url = str(request.url)
    if not verify_twilio_signature(
        auth_token=s.twilio_auth_token,
        url=url,
        form=form,
        signature=x_twilio_signature or "",
    ):
        raise AuthError(403, "invalid_twilio_signature", "Webhook signature is invalid.")

    # Twilio sends MessageSid+MessageStatus for SMS, CallSid+CallStatus
    # for voice. Try both — whichever set is populated wins.
    sid = form.get("MessageSid") or form.get("CallSid")
    raw_status = form.get("MessageStatus") or form.get("CallStatus")
    channel = (
        NotificationChannel.SMS.value
        if "MessageSid" in form
        else NotificationChannel.VOICE.value
    )
    if not sid or not raw_status:
        # Missing the fields we need — accept (sig was valid) but report
        # nothing matched so the operator can find this in audit-free
        # logs without a retry storm.
        return WebhookStatusOut(accepted=True, matched=False)

    mapped = map_twilio_status(channel, raw_status)
    if mapped is None:
        # Intermediate state — `accepted` so Twilio stops retrying;
        # `matched: false` because we deliberately did not write.
        return WebhookStatusOut(accepted=True, matched=False)

    error_message = form.get("ErrorMessage") or form.get("ErrorCode")
    updated = emergency_service.apply_provider_status(
        session,
        provider_ref=sid,
        channel=channel,
        new_status=mapped,
        error=error_message if mapped is NotificationStatus.FAILED else None,
        from_ip=client_ip(request),
    )
    return WebhookStatusOut(accepted=True, matched=updated is not None)


@router.post("/fcm/ack", response_model=FcmAckOut)
def acknowledge_fcm_delivery(
    body: FcmAckIn,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> FcmAckOut:
    """Mobile-side acknowledgment that an FCM push reached the device.
    Slice 16 review #1 — open to any authenticated user (the on-call
    doctor / nurse / ops / family / security_desk are all valid
    recipients per Slice 6 fan-out; the resident is NOT a push
    recipient in the current flow). The owner check inside the service
    is the security boundary: ``attempt.recipient_id == user.id`` or
    the call returns 404 with no existence leak. Idempotent — a second
    ack from the same device is a no-op (no duplicate audit row).
    """
    attempt = emergency_service.acknowledge_fcm_delivery(
        session,
        user=user,
        attempt_id=body.attempt_id,
        from_ip=client_ip(request),
    )
    return FcmAckOut(status=attempt.status, case_id=str(attempt.case_id))
