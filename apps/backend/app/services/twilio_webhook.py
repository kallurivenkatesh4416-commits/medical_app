"""Slice 16 — Twilio webhook utilities.

Two pieces:

1. **Signature validation** — Twilio signs every `StatusCallback` POST
   with HMAC-SHA1 of (URL + sorted form params), keyed by the Twilio
   `AUTH_TOKEN`. The handler MUST reject any request whose
   `X-Twilio-Signature` does not match, otherwise an unauthenticated
   attacker could write fake `delivered`/`failed` status rows. Constant-
   time compare via `hmac.compare_digest`.

2. **Provider-status → `NotificationStatus` mapping** — Twilio uses
   different state values for SMS (`MessageStatus`) and voice
   (`CallStatus`). The map collapses both into the small set the
   `notification_attempts` row carries.

No PHI in either path: the only thing the webhook updates is the row
status + an optional error code (Twilio's ErrorCode / ErrorMessage).
The Slice 6 invariant holds: a webhook touching one channel cannot
affect another channel's row.
"""

import base64
import hashlib
import hmac
from collections.abc import Mapping

from app.enums import NotificationChannel, NotificationStatus


def verify_twilio_signature(
    *,
    auth_token: str,
    url: str,
    form: Mapping[str, str],
    signature: str,
) -> bool:
    """Validates a Twilio webhook signature per
    https://www.twilio.com/docs/usage/security#validating-requests.

    The signed string is: full webhook URL (scheme, host, path, query) +
    sorted form params concatenated as ``key1value1key2value2...``.
    """
    if not auth_token or not signature:
        return False
    sorted_params = "".join(f"{k}{form[k]}" for k in sorted(form.keys()))
    payload = f"{url}{sorted_params}"
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    expected = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(expected, signature)


# SMS MessageStatus values that mean "final" — the provider has either
# confirmed delivery or given up. Intermediate states (queued / sending /
# sent) are *already* the synchronous response shape we record at send
# time; the webhook is only interesting for the terminal transition.
_SMS_DELIVERED = {"delivered", "read"}
_SMS_FAILED = {"undelivered", "failed", "canceled"}

# Voice CallStatus — same idea. `completed` is the terminal "delivered".
_VOICE_DELIVERED = {"completed"}
_VOICE_FAILED = {"busy", "failed", "no-answer", "canceled"}


def map_twilio_status(channel: str, raw: str) -> NotificationStatus | None:
    """Returns the new ``NotificationStatus`` to write, or ``None`` if
    the webhook event is an intermediate state we deliberately skip. The
    caller treats ``None`` as a no-op so a Twilio retry of the same
    event becomes safe."""
    raw = (raw or "").strip().lower()
    if channel == NotificationChannel.SMS.value:
        if raw in _SMS_DELIVERED:
            return NotificationStatus.DELIVERED
        if raw in _SMS_FAILED:
            return NotificationStatus.FAILED
        return None
    if channel == NotificationChannel.VOICE.value:
        if raw in _VOICE_DELIVERED:
            return NotificationStatus.DELIVERED
        if raw in _VOICE_FAILED:
            return NotificationStatus.FAILED
        return None
    # WhatsApp / unknown channel: no mapping in this slice. Slice 8
    # handover dispatches are tracked separately.
    return None
