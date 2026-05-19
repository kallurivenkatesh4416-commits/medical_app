"""NotificationGateway abstraction (brief §2.2 / §13, PLAN.md provider
abstraction). Business logic depends only on this Protocol; the concrete
provider is chosen by PROVIDER_MODE.

Slice 2 only needs SMS (OTP delivery). Push + voice land in Slices 5/6; their
methods are declared now so the interface is stable and swappable.
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
    # Live Twilio/FCM gateway lands with Slices 5/6 (needs the user's keys).
    raise NotImplementedError(
        f"provider_mode='{mode}' not available until the notification slices"
    )
