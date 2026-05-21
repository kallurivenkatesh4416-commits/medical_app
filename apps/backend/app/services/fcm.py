"""Slice 16 — live FCM HTTP v1 push gateway.

Replaces the ``NotImplementedError`` placeholder ``send_push`` had on the
Slice 6/8 Twilio gateway. Implements *only* the push channel; SMS / voice /
WhatsApp continue to flow through ``TwilioNotificationGateway`` (composed
together by ``get_notification_gateway``).

Auth path: ``google.oauth2.service_account.Credentials`` loaded from the
``FCM_SERVICE_ACCOUNT_FILE`` JSON. The library handles OAuth2 token mint /
refresh / clock-skew. The user picked this path explicitly for the
patient-safety push surface so credential edge cases stay on the official
library, not hand-rolled. ``google-auth`` is the only new backend dep
landing in this slice.

Send path: a single POST to
``https://fcm.googleapis.com/v1/projects/{project_id}/messages:send`` with
the credential's bearer token. Returns FCM's ``name`` (which embeds the
message id Twilio-style); the caller stores it as
``notification_attempts.provider_ref`` so the mobile-device FCM ack
endpoint can match deliveries back to the originating attempt.

PHI: brief §10 + Slice 6 invariant — the body never carries symptoms,
vitals, history, or medical record content. The gateway forwards exactly
what the service hands it (currently the constant ``_ALERT_BODY`` from
``emergency_service``: "A resident needs medical help, open the app").
"""

import json
from pathlib import Path
from typing import Any

import requests

from app.config import get_settings
from app.logging import get_logger

_log = get_logger("fcm")

_FCM_V1_BASE = "https://fcm.googleapis.com/v1"
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
# Network timeout — emergency push must fail fast so the fan-out moves on
# to SMS / voice instead of hanging the request.
_FCM_TIMEOUT_SECONDS = 10


class FcmConfigError(RuntimeError):
    """Raised when live FCM is selected but its env vars are missing or
    the service-account file is malformed. The fan-out catches this on
    `send_push` and records the FCM attempt as failed without blocking
    SMS / voice (Slice 6 invariant: one channel down -> the other two
    still fire)."""


class FcmPushGateway:
    """Lazy-loading FCM HTTP v1 gateway. The service-account file is read
    on the first ``send_push`` call so a missing/malformed file does NOT
    block app startup — it only fails the FCM channel, mirroring the way
    Twilio missing-sender errors surface today."""

    def __init__(
        self,
        *,
        service_account_file: str | None = None,
        project_id: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        s = get_settings()
        self._service_account_file = service_account_file or s.fcm_service_account_file
        self._project_id = project_id or s.fcm_project_id
        self._session = session or requests.Session()
        # Lazily-built; first send constructs and caches.
        self._credentials: Any | None = None

    def _messages_url(self) -> str:
        if not self._project_id:
            raise FcmConfigError(
                "FCM_PROJECT_ID must be set when PROVIDER_MODE=live with "
                "push enabled; see .env.example"
            )
        return f"{_FCM_V1_BASE}/projects/{self._project_id}/messages:send"

    def _ensure_credentials(self) -> Any:
        if self._credentials is not None:
            return self._credentials
        if not self._service_account_file:
            raise FcmConfigError(
                "FCM_SERVICE_ACCOUNT_FILE must be set when PROVIDER_MODE=live "
                "with push enabled; see .env.example"
            )
        # Local import — `google-auth` is only needed when the live path is
        # actually exercised; importing at module top would pull the dep into
        # every test that constructs the stub gateway too.
        from google.oauth2 import service_account  # noqa: PLC0415

        path = Path(self._service_account_file)
        if not path.is_file():
            raise FcmConfigError(
                f"FCM service account file not found: {self._service_account_file}"
            )
        try:
            creds = service_account.Credentials.from_service_account_file(
                str(path), scopes=[_FCM_SCOPE]
            )
        except Exception as exc:
            raise FcmConfigError(
                f"FCM service account file is malformed: {exc}"
            ) from exc
        self._credentials = creds
        return creds

    def _bearer(self) -> str:
        """Returns a live access token. The library refreshes when the
        token has expired (or is close to it — `google-auth` uses a 30s
        skew buffer)."""
        creds = self._ensure_credentials()
        # Local import for the same reason as `service_account` above.
        from google.auth.transport.requests import Request as GoogleAuthRequest  # noqa: PLC0415

        if not creds.valid:
            creds.refresh(GoogleAuthRequest())
        token = creds.token
        if not isinstance(token, str) or not token:
            raise FcmConfigError("FCM credentials returned an empty access token")
        return token

    def send_push(self, *, token: str, title: str, body: str) -> str:
        """POSTs one FCM HTTP v1 message. Returns the ``name`` field —
        which uniquely identifies the message — so the caller can store
        it as ``provider_ref`` and the mobile-device ack endpoint can
        match the delivery back to the originating attempt.

        PHI-safe: never logs `body` or `token`; Slice 6 stub gateway
        masks the recipient the same way.
        """
        payload = {
            "message": {
                "token": token,
                "notification": {"title": title, "body": body},
                # Slice 14 mobile receives the FCM ack on the data channel;
                # `provider_ref` lets the device POST back which attempt
                # was delivered. The mobile push handler reads this from
                # the data section (Android+iOS both surface it).
                "data": {"provider_ref_marker": "v1"},
                # Best-effort high priority for emergency alerts. Quietly
                # ignored on iOS; on Android it improves doze-mode delivery.
                "android": {"priority": "high"},
                "apns": {"headers": {"apns-priority": "10"}},
            }
        }
        _log.info("fcm_push", project_id=self._project_id)
        resp = self._session.post(
            self._messages_url(),
            headers={
                "Authorization": f"Bearer {self._bearer()}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=_FCM_TIMEOUT_SECONDS,
        )
        if resp.status_code == 401:
            # One forced refresh + retry — a clock-skew-induced 401 should
            # not bubble up as a permanent failure. The library's `valid`
            # check uses a local clock, so a peer-system skew can still
            # produce a 401 on the first call.
            from google.auth.transport.requests import Request as GoogleAuthRequest  # noqa: PLC0415

            self._credentials.refresh(GoogleAuthRequest())  # type: ignore[union-attr]
            resp = self._session.post(
                self._messages_url(),
                headers={
                    "Authorization": f"Bearer {self._bearer()}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=_FCM_TIMEOUT_SECONDS,
            )
        resp.raise_for_status()
        result = resp.json()
        # FCM v1 response shape: { "name": "projects/<id>/messages/<msg_id>" }
        # We store the full `name` so the mobile ack can match on either
        # the full string or the trailing msg id.
        name = result.get("name") if isinstance(result, dict) else None
        if not isinstance(name, str) or not name:
            raise FcmConfigError(
                "FCM send returned no `name` field; cannot record provider_ref"
            )
        return name
