"""Test config. Uses an in-memory SQLite DB so tests need zero infra."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("PROVIDER_MODE", "stub")

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())
