"""Slice 16 — FcmPushGateway tests.

The live FCM HTTP v1 gateway needs:
  1. A valid service account file to load OAuth2 credentials.
  2. A project id for the messages:send URL.
  3. A working access token to POST with.

We stub all three with monkeypatches so the test never touches the
network and never reads a real JSON key. The assertions focus on the
contract the rest of the slice depends on:

  - Authorization: Bearer <token> header is set on the FCM POST.
  - URL ends with `/projects/<id>/messages:send`.
  - The JSON body carries the device token + title + body verbatim.
  - The returned string equals the FCM `name` field, so the caller
    stores it as `notification_attempts.provider_ref` and the mobile
    ack endpoint can match it back.
  - A 401 on the first POST triggers a single credential refresh +
    retry (clock-skew recovery).
  - Missing project id or service account file raises FcmConfigError,
    which Slice 6's fan-out logs and swallows without blocking SMS/voice.
"""

from typing import Any

import pytest

from app.services import fcm as fcm_module
from app.services.fcm import FcmConfigError, FcmPushGateway


class _FakeCreds:
    """Mimics ``google.oauth2.service_account.Credentials`` for tests."""

    def __init__(self, *, token: str = "tok-1", valid: bool = True) -> None:
        self.token = token
        self.valid = valid
        self.refresh_calls = 0

    def refresh(self, _request: object) -> None:
        self.refresh_calls += 1
        self.valid = True
        # On refresh, mint a new token so the test can prove the retry
        # used the post-refresh value (not the stale 401-causing one).
        self.token = f"{self.token}-refreshed"


class _FakeResp:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body or {"name": "projects/demo/messages/msg-1"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            from requests.exceptions import HTTPError

            raise HTTPError(f"{self.status_code} fcm error")

    def json(self) -> dict[str, Any]:
        return self._json


class _FakeSession:
    def __init__(self, *, responses: list[_FakeResp]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, headers: dict[str, str], data: bytes, timeout: int):
        self.calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
        if not self._responses:
            return _FakeResp()
        return self._responses.pop(0)


def _stub_credentials_loader(monkeypatch: pytest.MonkeyPatch, creds: _FakeCreds) -> None:
    """Replaces ``service_account.Credentials.from_service_account_file`` so
    no real JSON is read. ``from_service_account_file`` lives behind a
    late import inside ``_ensure_credentials`` — patching the parent module
    works because both calls go through the same identity."""

    class _ServiceAccountModule:
        class Credentials:
            @staticmethod
            def from_service_account_file(path: str, scopes: list[str]) -> _FakeCreds:
                _ = (path, scopes)
                return creds

    class _GoogleOauth2:
        service_account = _ServiceAccountModule

    class _GoogleAuthTransportRequests:
        class Request:
            def __init__(self) -> None:
                pass

    # The gateway uses local imports — we shim the modules it imports into
    # `sys.modules` so the local `from google.oauth2 import service_account`
    # and `from google.auth.transport.requests import Request` both resolve
    # to our fakes without touching the real google-auth package.
    import sys

    monkeypatch.setitem(sys.modules, "google.oauth2", _GoogleOauth2)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", _ServiceAccountModule)
    monkeypatch.setitem(
        sys.modules,
        "google.auth.transport.requests",
        _GoogleAuthTransportRequests,
    )


def _settings_with(*, file: str | None, project: str | None) -> Any:
    class _FakeSettings:
        fcm_service_account_file = file
        fcm_project_id = project

    return _FakeSettings()


def test_send_push_posts_to_v1_url_with_bearer_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Happy-path: bearer + URL + payload contract."""
    sa = tmp_path / "fake-sa.json"
    sa.write_text("{}")  # contents are ignored — we stub the loader

    monkeypatch.setattr(
        fcm_module, "get_settings", lambda: _settings_with(file=str(sa), project="demo-1")
    )
    creds = _FakeCreds(token="bearer-A")
    _stub_credentials_loader(monkeypatch, creds)

    session = _FakeSession(
        responses=[_FakeResp(json_body={"name": "projects/demo-1/messages/abc"})]
    )
    gw = FcmPushGateway(session=session)  # type: ignore[arg-type]

    name = gw.send_push(token="dev-token-1", title="Emergency alert", body="open app")

    assert name == "projects/demo-1/messages/abc"
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"].endswith("/v1/projects/demo-1/messages:send")
    assert call["headers"]["Authorization"] == "Bearer bearer-A"
    assert call["headers"]["Content-Type"] == "application/json"
    # JSON body shape — the device token is in `message.token`, the
    # notification title/body is forwarded verbatim. The `data` map
    # carries the v1 marker the mobile ack handler reads to know the
    # provider_ref shape.
    import json as _json

    body = _json.loads(call["data"])
    assert body["message"]["token"] == "dev-token-1"
    assert body["message"]["notification"]["title"] == "Emergency alert"
    assert body["message"]["notification"]["body"] == "open app"
    assert body["message"]["android"]["priority"] == "high"


def test_401_triggers_one_refresh_and_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Clock-skew recovery: a 401 forces one credential refresh; the
    retry uses the refreshed token; the second 200 returns successfully."""
    sa = tmp_path / "fake-sa.json"
    sa.write_text("{}")

    monkeypatch.setattr(
        fcm_module, "get_settings", lambda: _settings_with(file=str(sa), project="demo-2")
    )
    creds = _FakeCreds(token="bearer-A")
    _stub_credentials_loader(monkeypatch, creds)

    session = _FakeSession(
        responses=[
            _FakeResp(status_code=401),
            _FakeResp(json_body={"name": "projects/demo-2/messages/xyz"}),
        ]
    )
    gw = FcmPushGateway(session=session)  # type: ignore[arg-type]

    name = gw.send_push(token="dev-token-2", title="t", body="b")

    assert name == "projects/demo-2/messages/xyz"
    assert creds.refresh_calls == 1, "401 must force exactly one refresh"
    assert len(session.calls) == 2
    # Retry rides the refreshed token.
    assert session.calls[1]["headers"]["Authorization"] == "Bearer bearer-A-refreshed"


def test_missing_project_id_raises_fcm_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    sa = tmp_path / "fake-sa.json"
    sa.write_text("{}")
    monkeypatch.setattr(
        fcm_module, "get_settings", lambda: _settings_with(file=str(sa), project=None)
    )
    _stub_credentials_loader(monkeypatch, _FakeCreds())

    gw = FcmPushGateway(session=_FakeSession(responses=[]))  # type: ignore[arg-type]
    with pytest.raises(FcmConfigError, match="FCM_PROJECT_ID"):
        gw.send_push(token="t", title="x", body="y")


def test_missing_service_account_file_raises_fcm_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fcm_module, "get_settings", lambda: _settings_with(file=None, project="demo")
    )
    gw = FcmPushGateway(session=_FakeSession(responses=[]))  # type: ignore[arg-type]
    with pytest.raises(FcmConfigError, match="FCM_SERVICE_ACCOUNT_FILE"):
        gw.send_push(token="t", title="x", body="y")


def test_missing_file_on_disk_raises_fcm_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(
        fcm_module,
        "get_settings",
        lambda: _settings_with(
            file=str(tmp_path / "nope.json"), project="demo"
        ),
    )
    gw = FcmPushGateway(session=_FakeSession(responses=[]))  # type: ignore[arg-type]
    with pytest.raises(FcmConfigError, match="not found"):
        gw.send_push(token="t", title="x", body="y")


def test_fcm_response_without_name_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """If FCM returned 200 but no `name` field we cannot record provider_ref,
    so the gateway raises and the fan-out logs FCM as failed without
    blocking SMS / voice (Slice 6 invariant)."""
    sa = tmp_path / "fake-sa.json"
    sa.write_text("{}")
    monkeypatch.setattr(
        fcm_module, "get_settings", lambda: _settings_with(file=str(sa), project="demo")
    )
    _stub_credentials_loader(monkeypatch, _FakeCreds())

    session = _FakeSession(responses=[_FakeResp(json_body={"unexpected": True})])
    gw = FcmPushGateway(session=session)  # type: ignore[arg-type]
    with pytest.raises(FcmConfigError, match="no `name`"):
        gw.send_push(token="t", title="x", body="y")
