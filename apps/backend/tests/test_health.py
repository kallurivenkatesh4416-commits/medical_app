"""Slice 1 smoke tests for the health endpoints and error envelope."""

from fastapi.testclient import TestClient


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
    for banned in ("diagnose", "diagnosis", "heart attack detected", "stroke detected"):
        assert banned not in schema
