"""Test config.

Default runs stay in-memory SQLite and need zero infra. Tests explicitly marked
``postgres_only`` may opt into a `_test` Postgres database so row-lock paths
can be exercised without letting the destructive cleanup fixture touch a
production-looking URL.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, SQLModel

# Default to zero-infra SQLite. The Postgres-only CI lane sets an explicit test
# DATABASE_URL before pytest imports this module.
_database_url = os.environ.get("DATABASE_URL", "")
if not _database_url.startswith(("postgresql", "postgres")):
    os.environ["DATABASE_URL"] = "sqlite://"
os.environ["PROVIDER_MODE"] = "stub"
os.environ["APP_ENV"] = "local"

from app.db import engine  # noqa: E402
from app.enums import Role  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.user import User  # noqa: E402

# Hard guard: destructive fixtures run only against in-memory SQLite or a
# Postgres database with an explicit test suffix.
_backend = engine.url.get_backend_name()
_database = engine.url.database or ""
assert _backend == "sqlite" or (_backend == "postgresql" and _database.endswith("_test")), (
    "tests must run on SQLite or a `_test` Postgres DB, "
    f"got {_backend} database {_database!r}"
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if _backend == "postgresql":
        return
    skip_postgres = pytest.mark.skip(reason="requires a Postgres test database")
    for item in items:
        if "postgres_only" in item.keywords:
            item.add_marker(skip_postgres)


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    SQLModel.metadata.create_all(engine)
    yield
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> Iterator[None]:
    if _backend == "postgresql":
        _clear_tables()
    yield
    _clear_tables()


def _clear_tables() -> None:
    # Core-level deletes bypass the audit_log ORM immutability guard so tests
    # stay isolated without weakening the production guarantee.
    with engine.begin() as conn:
        if _backend == "postgresql":
            table_names = ", ".join(table.name for table in SQLModel.metadata.sorted_tables)
            conn.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
            return
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
