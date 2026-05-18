"""Database engine + session (SQLModel / SQLAlchemy 2.x).

Models and migrations land from Slice 2; this module exists so health checks can
verify DB connectivity and Alembic can target the metadata.
"""

from collections.abc import Iterator

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_settings = get_settings()

_connect_args = (
    {"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {}
)

engine = create_engine(_settings.database_url, connect_args=_connect_args, pool_pre_ping=True)

# Shared metadata target for Alembic autogenerate (populated by models in Slice 2).
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
