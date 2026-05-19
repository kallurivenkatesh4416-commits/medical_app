"""Database engine + session (SQLModel / SQLAlchemy 2.x)."""

from collections.abc import Iterator

from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_settings = get_settings()
_url = _settings.database_url


def _make_engine():
    if _url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # In-memory SQLite needs a single shared connection or every session
        # gets its own empty database (breaks tests + TestClient threads).
        is_memory = _url in ("sqlite://", "sqlite:///:memory:")
        if is_memory:
            return create_engine(
                _url, connect_args=connect_args, poolclass=StaticPool
            )
        return create_engine(_url, connect_args=connect_args)
    return create_engine(_url, pool_pre_ping=True)


engine = _make_engine()

# Importing the models registers them on SQLModel.metadata (used by Alembic and
# by create_all in tests). Imported here so any DB consumer sees the tables.
from app import models  # noqa: E402,F401

# Shared metadata target for Alembic autogenerate.
metadata = SQLModel.metadata


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


def check_database() -> bool:
    """Return True if a trivial query succeeds (used by /readyz)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
