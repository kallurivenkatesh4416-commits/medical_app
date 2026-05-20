"""NotificationGateway abstraction (brief §2.2 / §13, PLAN.md provider
abstraction). Business logic depends only on this Protocol; the concrete
provider is chosen by PROVIDER_MODE.

Slice 2 only needs SMS (OTP delivery). Push lands in Slice 5; the Slice 6
emergency fan-out exercises all three (`send_push` + `send_sms` +
`place_voice_call`) and logs each independently. The stub returns a stable
provider ref per channel so the whole fan-out — including "kill push, others
still deliver" — is testable with zero external deps.

Slice 6/8 add the live Twilio implementation for SMS / voice / WhatsApp
(``TwilioNotificationGateway`` below). FCM push live wiring is intentionally
NOT implemented here — its credential model is different (service-account
file) and the operator-keys task tracks that separately; calling
``send_push`` on the live gateway raises and Slice 6's fan-out records that
channel as failed without blocking the others.
"""

from typing import Protocol

import requests
from requests.auth import HTTPBasicAuth

from app.config import get_settings
from app.logging import get_logger

_log = get_logger("notifications")

_TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"
# Network timeout for live Twilio calls — emergency channels must fail fast
# so the fan-out moves on to the next channel instead of hanging the request.
_TWILIO_TIMEOUT_SECONDS = 10


def _mask(recipient: str) -> str:
    return f"***{recipient[-2:]}" if len(recipient) >= 2 else "***"


class NotificationGateway(Protocol):
    def send_sms(self, *, to: str, body: str) -> str: ...

    def send_push(self, *, token: str, title: str, body: str) -> str: ...

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

    def send_push(self, *, token: str, title: str, body: str) -> str:
        _log.info("stub_push")
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

    def _messages_url(self) -> str:
        return f"{_TWILIO_API_BASE}/Accounts/{self._account_sid}/Messages.json"

    def _calls_url(self) -> str:
        return f"{_TWILIO_API_BASE}/Accounts/{self._account_sid}/Calls.json"

    def _post_message(self, *, to: str, body: str, sender: str | None, channel: str) -> str:
        if not sender:
            raise RuntimeError(f"twilio {channel} sender is not configured")
        resp = requests.post(
            self._messages_url(),
            auth=self._auth,
            data={"From": sender, "To": to, "Body": body},
            timeout=_TWILIO_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("sid", "")

    def send_sms(self, *, to: str, body: str) -> str:
        _log.info("twilio_sms", to=_mask(to))
        return self._post_message(to=to, body=body, sender=self._sms_from, channel="sms")

    def send_push(self, *, token: str, title: str, body: str) -> str:
        # FCM is intentionally NOT routed through Twilio. The live FCM service
        # account is tracked as a separate operator-keys task; until that
        # lands, calling this on the live gateway raises and Slice 6's
        # fan-out marks the FCM attempt failed without blocking SMS / voice.
        raise NotImplementedError(
            "FCM live wiring requires a service-account file (FCM_SERVICE_ACCOUNT_FILE); "
            "configure that and replace this method, or fan-out will degrade gracefully"
        )

    def place_voice_call(self, *, to: str, twiml_url: str) -> str:
        if not self._voice_from:
            raise RuntimeError("twilio voice sender is not configured")
        _log.info("twilio_voice", to=_mask(to))
        resp = requests.post(
            self._calls_url(),
            auth=self._auth,
            data={"From": self._voice_from, "To": to, "Url": twiml_url},
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


def get_notification_gateway() -> NotificationGateway:
    mode = get_settings().provider_mode
    if mode == "stub":
        return StubNotificationGateway()
    if mode == "live":
        # Live Twilio for SMS / voice / WhatsApp; FCM raises in `send_push`
        # until its separate live wiring lands (see TwilioNotificationGateway
        # docstring).
        return TwilioNotificationGateway()
    raise NotImplementedError(
        f"provider_mode='{mode}' is not a known mode; use 'stub' or 'live'"
    )
