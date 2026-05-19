"""Test config. In-memory SQLite (StaticPool) so tests need zero infra."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("PROVIDER_MODE", "stub")
os.environ.setdefault("APP_ENV", "local")

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

from app.db import engine
from app.enums import Role
from app.main import create_app
from app.models.project import Project
from app.models.user import User


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    SQLModel.metadata.create_all(engine)
    yield
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    yield
    # Core-level deletes bypass the audit_log ORM immutability guard so tests
    # stay isolated without weakening the production guarantee.
    with engine.begin() as conn:
        for table in reversed(SQLModel.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def session() -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def project(session: Session) -> Project:
    p = Project(name="Test Residency", enable_security_desk_alerts=True)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


@pytest.fixture
def make_user(session: Session, project: Project):
    def _make(role: Role, phone: str | None = None) -> User:
        u = User(
            project_id=None if role is Role.SUPER_ADMIN else project.id,
            phone=phone or f"+15550{uuid.uuid4().int % 100000:05d}",
            role=role.value,
            full_name=f"Test {role.value}",
        )
        session.add(u)
        session.commit()
        session.refresh(u)
        return u

    return _make


@pytest.fixture
def login(client: TestClient):
    """Complete OTP login for an existing phone; return the TokenPair dict."""

    def _login(phone: str) -> dict[str, str]:
        req = client.post("/api/v1/auth/otp/request", json={"phone": phone})
        assert req.status_code == 200, req.text
        code = req.json()["dev_otp"]
        assert code, "dev_otp must be exposed when APP_ENV=local"
        vr = client.post(
            "/api/v1/auth/otp/verify", json={"phone": phone, "code": code}
        )
        assert vr.status_code == 200, vr.text
        return vr.json()

    return _login
