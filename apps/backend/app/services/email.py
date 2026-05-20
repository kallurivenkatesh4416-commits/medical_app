"""EmailGateway abstraction (PLAN.md Slice 8 / brief §2.3 provider abstraction).

Business logic depends only on this Protocol; the concrete backend is chosen
by ``PROVIDER_MODE``.

- ``stub`` -> log + capture in-process (used by tests to assert dispatch
  without a network round-trip). Never opens a socket.
- ``live`` -> SMTP send (e.g. Mailhog in docker-compose; SES/Mailgun in prod
  via standard SMTP credentials).

The handover dispatch never embeds the PDF body — outbound mail carries only
the short-lived signed link, so revocation/expiry stays enforceable.
"""

import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import get_settings
from app.logging import get_logger

_log = get_logger("email")


def _mask(addr: str) -> str:
    if "@" not in addr:
        return f"***{addr[-2:]}"
    local, _, domain = addr.partition("@")
    visible = local[:1] or "*"
    return f"{visible}***@{domain}"


class EmailGateway(Protocol):
    def send(self, *, to: str, subject: str, body: str) -> str: ...


class StubEmailGateway:
    """Dev/CI stub — logs a masked recipient and returns a stable provider
    ref. Tests reach into ``StubEmailGateway.sent`` (class attribute) to
    assert what would have shipped without standing up SMTP."""

    sent: list[dict[str, str]] = []

    def send(self, *, to: str, subject: str, body: str) -> str:
        # Body intentionally NOT logged — it contains the signed handover URL
        # which is a capability token. Tests inspect via .sent.
        StubEmailGateway.sent.append({"to": to, "subject": subject, "body": body})
        _log.info("stub_email", to=_mask(to), subject=subject)
        return f"stub-email-{len(StubEmailGateway.sent)}"


class SmtpEmailGateway:
    """Live SMTP gateway. Mailhog (docker-compose dev) needs no auth/TLS;
    SES/Mailgun set the credentials through ``SMTP_*`` env vars."""

    def send(self, *, to: str, subject: str, body: str) -> str:
        settings = get_settings()
        msg = EmailMessage()
        msg["From"] = settings.handover_email_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)
        return f"smtp:{to}"


def get_email_gateway() -> EmailGateway:
    if get_settings().provider_mode == "stub":
        return StubEmailGateway()
    return SmtpEmailGateway()
