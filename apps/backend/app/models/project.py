"""Project — multi-tenant root (brief §6). Same backend serves many builder
projects; almost every other table is scoped by project_id."""

from sqlmodel import Field

from app.models.base import TimestampMixin, UUIDPKMixin


class Project(UUIDPKMixin, TimestampMixin, table=True):
    __tablename__ = "projects"

    name: str = Field(index=True)
    # Brief addition: security-desk alerts are opt-in per project.
    enable_security_desk_alerts: bool = Field(default=False)
