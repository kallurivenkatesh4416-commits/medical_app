"""Slice 1 smoke tests for the health endpoints and error envelope."""

from fastapi.testclient import TestClient
from pydantic import BaseModel


def test_healthz_liveness(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_ready_when_db_reachable(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready"}


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    resp = client.get("/does-not-exist")
    assert resp.status_code == 404
    body = resp.json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "http_404"


def test_openapi_has_no_diagnosis_language(client: TestClient) -> None:
    # Guardrail (brief §2.1 / §13): no diagnosis claims in the public schema.
    schema = client.get("/openapi.json").text.lower()
    for banned in (
        "diagnose",
        "diagnosis",
        "diagnostic",
        "heart attack detected",
        "stroke detected",
        "you have",
    ):
        assert banned not in schema


def test_validation_error_uses_error_envelope(client: TestClient) -> None:
    # Validation failures must use the same envelope and must NOT echo the raw
    # offending input back to the client (could be PHI / secrets).
    class _Body(BaseModel):
        patient_secret: int

    @client.app.post("/_test/validate")
    def _v(_: _Body) -> dict[str, str]:  # pragma: no cover - exercised via client
        return {"ok": "true"}

    resp = client.post("/_test/validate", json={"patient_secret": "leak-me-1234"})
    assert resp.status_code == 422
    body = resp.json()
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == "validation_error"
    assert "leak-me-1234" not in resp.text
