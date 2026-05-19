"""NotificationGateway abstraction (brief §2.2 / §13, PLAN.md provider
abstraction). Business logic depends only on this Protocol; the concrete
provider is chosen by PROVIDER_MODE.

Slice 2 only needs SMS (OTP delivery). Push lands in Slice 5; the Slice 6
emergency fan-out exercises all three (`send_push` + `send_sms` +
`place_voice_call`) and logs each independently. The stub returns a stable
provider ref per channel so the whole fan-out — including "kill push, others
still deliver" — is testable with zero external deps. The concrete live
Twilio/FCM gateway is wired on `provider_mode='live'` once the operator
supplies keys (PLAN.md Slice 6 "Needs your keys: Twilio + FCM").
"""

from typing import Protocol

from app.config import get_settings
from app.logging import get_logger

_log = get_logger("notifications")


def _mask(recipient: str) -> str:
    return f"***{recipient[-2:]}" if len(recipient) >= 2 else "***"


class NotificationGateway(Protocol):
    def send_sms(self, *, to: str, body: str) -> str: ...

    def send_push(self, *, token: str, title: str, body: str) -> str: ...

    def place_voice_call(self, *, to: str, twiml_url: str) -> str: ...


class StubNotificationGateway:
    """Console/log stub — zero external deps, used in dev/CI. Never logs the
    message body (an OTP is a secret); only a masked recipient + channel."""

    def send_sms(self, *, to: str, body: str) -> str:
        _log.info("stub_sms", to=_mask(to))
        return "stub-sms"

    def send_push(self, *, token: str, title: str, body: str) -> str:
        _log.info("stub_push")
        return "stub-push"

    def place_voice_call(self, *, to: str, twiml_url: str) -> str:
        _log.info("stub_voice", to=_mask(to))
        return "stub-voice"


def get_notification_gateway() -> NotificationGateway:
    mode = get_settings().provider_mode
    if mode == "stub":
        return StubNotificationGateway()
    # The concrete live gateway is constructed here once the operator supplies
    # Twilio (TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_SMS_FROM /
    # TWILIO_VOICE_FROM) and FCM (FCM_SERVICE_ACCOUNT_FILE) credentials. The
    # fan-out orchestration, escalation, and fallback are provider-agnostic and
    # fully covered against the stub; live wiring is verified with real keys
    # (PLAN.md Slice 6 "Needs your keys: Twilio + FCM").
    raise NotImplementedError(
        f"provider_mode='{mode}' requires Twilio + FCM credentials "
        "(see .env.example); only provider_mode='stub' runs without keys"
    )
