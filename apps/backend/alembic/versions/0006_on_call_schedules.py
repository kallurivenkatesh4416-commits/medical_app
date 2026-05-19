"""on-call schedules

Revision ID: 0006_on_call_schedules
Revises: 0005_emergency_happy_path
Create Date: 2026-05-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_on_call_schedules"
down_revision: str | None = "0005_emergency_happy_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "on_call_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("ends_at", sa.DateTime(), nullable=False),
        sa.Column("is_backup", sa.Boolean(), nullable=False),
        sa.Column("contact_phone", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_on_call_schedules_project_id", "on_call_schedules", ["project_id"])
    op.create_index("ix_on_call_schedules_role", "on_call_schedules", ["role"])
    op.create_index("ix_on_call_schedules_user_id", "on_call_schedules", ["user_id"])
    op.create_index("ix_on_call_schedules_starts_at", "on_call_schedules", ["starts_at"])
    op.create_index("ix_on_call_schedules_ends_at", "on_call_schedules", ["ends_at"])
    op.create_index("ix_on_call_schedules_is_backup", "on_call_schedules", ["is_backup"])


def downgrade() -> None:
    op.drop_table("on_call_schedules")
