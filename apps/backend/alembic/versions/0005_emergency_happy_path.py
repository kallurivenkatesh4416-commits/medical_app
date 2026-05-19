"""emergency happy path tables

Revision ID: 0005_emergency_happy_path
Revises: 0004_medical_records
Create Date: 2026-05-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_emergency_happy_path"
down_revision: str | None = "0004_medical_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emergency_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("resident_id", sa.Uuid(), sa.ForeignKey("residents.id"), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_doctor_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("alert_time", sa.DateTime(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("on_site_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("symptom_codes", sa.JSON(), nullable=True),
        sa.Column("location_text", sa.String(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("resolved_outcome", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_emergency_cases_project_id", "emergency_cases", ["project_id"])
    op.create_index("ix_emergency_cases_resident_id", "emergency_cases", ["resident_id"])
    op.create_index("ix_emergency_cases_status", "emergency_cases", ["status"])

    op.create_table(
        "notification_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("emergency_cases.id"), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("provider_ref", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_notification_attempts_project_id", "notification_attempts", ["project_id"]
    )
    op.create_index("ix_notification_attempts_case_id", "notification_attempts", ["case_id"])
    op.create_index("ix_notification_attempts_channel", "notification_attempts", ["channel"])
    op.create_index("ix_notification_attempts_status", "notification_attempts", ["status"])

    op.create_table(
        "case_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("emergency_cases.id"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("from_status", sa.String(), nullable=True),
        sa.Column("to_status", sa.String(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_case_events_project_id", "case_events", ["project_id"])
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])
    op.create_index("ix_case_events_event_type", "case_events", ["event_type"])

    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("push_token", sa.String(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_device_tokens_project_id", "device_tokens", ["project_id"])
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])
    op.create_index(
        "ix_device_tokens_push_token", "device_tokens", ["push_token"], unique=True
    )


def downgrade() -> None:
    op.drop_table("device_tokens")
    op.drop_table("case_events")
    op.drop_table("notification_attempts")
    op.drop_table("emergency_cases")
