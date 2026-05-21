"""NotificationGateway abstraction (brief §2.2 / §13, PLAN.md provider
abstraction). Business logic depends only on this Protocol; the concrete
provider is chosen by PROVIDER_MODE.

Slice 2 only needs SMS (OTP delivery). Push lands in Slice 5; the Slice 6
emergency fan-out exercises all three (`send_push` + `send_sms` +
`place_voice_call`) and logs each independently. The stub returns a stable
provider ref per channel so the whole fan-out — including "kill push, others
still deliver" — is testable with zero external deps.

Slice 6/8 added the live Twilio implementation for SMS / voice / WhatsApp
(``TwilioNotificationGateway`` below). Slice 16 lands live FCM push via
``FcmPushGateway`` (separate module — its credential / OAuth2 path is
distinct from Twilio's basic auth). ``CompositeGateway`` joins them so
``provider_mode='live'`` exercises all three channels for real; one
channel failing remains logged-and-swallowed by the Slice 6 fan-out.

When ``FCM_SERVICE_ACCOUNT_FILE`` is empty in live mode the composite
still constructs but ``send_push`` raises ``FcmConfigError``, mirroring
the way the old gateway raised ``NotImplementedError`` — Slice 6's
fan-out records FCM as failed without blocking SMS / voice.
"""

from typing import Protocol

import requests
from requests.auth import HTTPBasicAuth

from app.config import get_settings
from app.logging import get_logger
from app.services.fcm import FcmConfigError, FcmPushGateway

_log = get_logger("notifications")

_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
# Network timeout for live Twilio calls — emergency channels must fail fast
# so the fan-out moves on to the next channel instead of hanging the request.
_TWILIO_TIMEOUT_SECONDS = 10


def _mask(recipient: str) -> str:
    return f"***{recipient[-2:]}" if len(recipient) >= 2 else "***"


class NotificationGateway(Protocol):
    def send_sms(self, *, to: str, body: str) -> str: ...

    # Slice 16 review #1: `attempt_id` is the pre-existing
    # ``notification_attempts.id`` (UUID) for this push. It rides in the
    # FCM `data` payload so the receiving device can POST the same id
    # back to ``/api/v1/notifications/fcm/ack`` for delivery
    # confirmation. We CANNOT round-trip the FCM `name` (the provider
    # ref) the same way because FCM only returns it after the send call
    # completes — so the device-side ack matches on `attempt_id`, while
    # `notification_attempts.provider_ref` stays as the FCM `name` for
    # cross-referencing in the FCM console.
    def send_push(
        self, *, token: str, title: str, body: str, attempt_id: str
    ) -> str: ...

    def place_voice_call(self, *, to: str, twiml_url: str) -> str: ...

    # Slice 8: WhatsApp dispatch for the hospital handover signed link. Twilio
    # WhatsApp uses the same auth as SMS but a ``whatsapp:`` prefix on both
    # the sender and recipient — the gateway encapsulates that detail so
    # callers pass a normal phone number.
    def send_whatsapp(self, *, to: str, body: str) -> str: ...


class StubNotificationGateway:
    """Console/log stub — zero external deps, used in dev/CI. Never logs the
    message body (an OTP is a secret); only a masked recipient + channel.
    WhatsApp captures are kept on the class so tests can assert dispatch
    without standing up Twilio."""

    sent_whatsapp: list[dict[str, str]] = []

    def send_sms(self, *, to: str, body: str) -> str:
        _log.info("stub_sms", to=_mask(to))
        return "stub-sms"

    def send_push(
        self, *, token: str, title: str, body: str, attempt_id: str
    ) -> str:
        # `attempt_id` is logged so dev/CI can correlate stub pushes with
        # notification_attempts rows; the value is a UUID, never PHI.
        _log.info("stub_push", attempt_id=attempt_id)
        return "stub-push"

    def place_voice_call(self, *, to: str, twiml_url: str) -> str:
        _log.info("stub_voice", to=_mask(to))
        return "stub-voice"

    def send_whatsapp(self, *, to: str, body: str) -> str:
        StubNotificationGateway.sent_whatsapp.append({"to": to, "body": body})
        _log.info("stub_whatsapp", to=_mask(to))
        return f"stub-whatsapp-{len(StubNotificationGateway.sent_whatsapp)}"


class TwilioNotificationGateway:
    """Live Twilio gateway for SMS / voice / WhatsApp. Credentials come from
    the standard ``TWILIO_*`` env vars (see config.py / .env.example):

    - SMS   → ``twilio_sms_from`` (e.g. ``+15558675309``)
    - Voice → ``twilio_voice_from`` + a TwiML URL the gateway hosts
    - WhatsApp → ``twilio_whatsapp_from`` (e.g. ``whatsapp:+14155238886``);
      Twilio requires the ``whatsapp:`` prefix on BOTH endpoints — that
      detail stays inside the gateway so callers pass a normal phone number.

    Message body / push payloads are PHI-free by contract (Slice 6); the
    gateway forwards them verbatim and never logs them.
    """

    def __init__(self) -> None:
        s = get_settings()
        if not (s.twilio_account_sid and s.twilio_auth_token):
            raise RuntimeError(
                "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be set when "
                "PROVIDER_MODE=live; see .env.example"
            )
        self._auth = HTTPBasicAuth(s.twilio_account_sid, s.twilio_auth_token)
        self._account_sid = s.twilio_account_sid
        self._sms_from = s.twilio_sms_from
        self._voice_from = s.twilio_voice_from
        self._whatsapp_from = s.twilio_whatsapp_from
        # Slice 16: when configured, every Twilio send rides with a
        # `StatusCallback` so the provider posts delivery/failure state
        # back to us. The webhook handler validates X-Twilio-Signature
        # before updating `notification_attempts`. Leaving this blank
        # disables callbacks (`sent` from the synchronous REST call stays
        # the only signal — matches pre-Slice-16 behaviour).
        self._status_callback_url = s.twilio_status_callback_url

    def _messages_url(self) -> str:
        return f"{_TWILIO_API_BASE}/Accounts/{self._account_sid}/Messages.json"

    def _calls_url(self) -> str:
        return f"{_TWILIO_API_BASE}/Accounts/{self._account_sid}/Calls.json"

    def _post_message(self, *, to: str, body: str, sender: str | None, channel: str) -> str:
        if not sender:
            raise RuntimeError(f"twilio {channel} sender is not configured")
        data: dict[str, str] = {"From": sender, "To": to, "Body": body}
        if self._status_callback_url:
            data["StatusCallback"] = self._status_callback_url
        resp = requests.post(
            self._messages_url(),
            auth=self._auth,
            data=data,
            timeout=_TWILIO_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("sid", "")

    def send_sms(self, *, to: str, body: str) -> str:
        _log.info("twilio_sms", to=_mask(to))
        return self._post_message(to=to, body=body, sender=self._sms_from, channel="sms")

    def send_push(
        self, *, token: str, title: str, body: str, attempt_id: str
    ) -> str:
        # FCM is not a Twilio service. The composite gateway built by
        # `get_notification_gateway()` routes push to `FcmPushGateway`
        # instead of this method; this stays as a defensive fallback so a
        # direct caller (e.g. a test that constructs Twilio in isolation)
        # gets a clear error rather than a silent no-op. Slice 6's fan-out
        # logs this as the FCM attempt's failure and continues with SMS /
        # voice — patient-safety invariant preserved.
        del attempt_id  # logged via FcmConfigError below for visibility
        raise FcmConfigError(
            "TwilioNotificationGateway does not implement send_push. "
            "Use `get_notification_gateway()` to get the composite that "
            "routes push through FcmPushGateway."
        )

    def place_voice_call(self, *, to: str, twiml_url: str) -> str:
        if not self._voice_from:
            raise RuntimeError("twilio voice sender is not configured")
        _log.info("twilio_voice", to=_mask(to))
        data: dict[str, str] = {
            "From": self._voice_from,
            "To": to,
            "Url": twiml_url,
        }
        if self._status_callback_url:
            data["StatusCallback"] = self._status_callback_url
        resp = requests.post(
            self._calls_url(),
            auth=self._auth,
            data=data,
            timeout=_TWILIO_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("sid", "")

    def send_whatsapp(self, *, to: str, body: str) -> str:
        # Twilio WhatsApp requires the `whatsapp:` prefix on both endpoints.
        # Stripping any caller-supplied prefix first keeps the contract that
        # callers pass a normal phone number, even if the dashboard form ever
        # accidentally prefixes it.
        normalised = to.removeprefix("whatsapp:") if isinstance(to, str) else to
        _log.info("twilio_whatsapp", to=_mask(normalised))
        return self._post_message(
            to=f"whatsapp:{normalised}",
            body=body,
            sender=self._whatsapp_from,
            channel="whatsapp",
        )


class CompositeGateway:
    """Slice 16 — joins ``FcmPushGateway`` (push) and
    ``TwilioNotificationGateway`` (SMS / voice / WhatsApp) behind the
    single ``NotificationGateway`` protocol the emergency fan-out depends
    on. Slice 6's "one channel down, other two still fire" invariant
    holds because the fan-out catches exceptions per attempt — the
    composite just routes the call to the right vendor; it does NOT try
    to recover failures on its behalf."""

    def __init__(
        self,
        *,
        push: "NotificationGateway",
        messaging: "NotificationGateway",
    ) -> None:
        self._push = push
        self._messaging = messaging

    def send_push(
        self, *, token: str, title: str, body: str, attempt_id: str
    ) -> str:
        return self._push.send_push(
            token=token, title=title, body=body, attempt_id=attempt_id
        )

    def send_sms(self, *, to: str, body: str) -> str:
        return self._messaging.send_sms(to=to, body=body)

    def place_voice_call(self, *, to: str, twiml_url: str) -> str:
        return self._messaging.place_voice_call(to=to, twiml_url=twiml_url)

    def send_whatsapp(self, *, to: str, body: str) -> str:
        return self._messaging.send_whatsapp(to=to, body=body)


def get_notification_gateway() -> NotificationGateway:
    mode = get_settings().provider_mode
    if mode == "stub":
        return StubNotificationGateway()
    if mode == "live":
        # Slice 16: live = FCM (push) + Twilio (SMS/voice/WhatsApp).
        # Both classes lazy-load their credentials so the composite can be
        # constructed even when one set of keys is incomplete — the missing
        # channel surfaces as a failed attempt at send time, not at startup.
        return CompositeGateway(
            push=FcmPushGateway(),
            messaging=TwilioNotificationGateway(),
        )
    raise NotImplementedError(
        f"provider_mode='{mode}' is not a known mode; use 'stub' or 'live'"
    )
