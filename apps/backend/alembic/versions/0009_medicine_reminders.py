"""medicine schedules and dose logs

Revision ID: 0009_medicine_reminders
Revises: 0008_handover_pdfs
Create Date: 2026-05-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_medicine_reminders"
down_revision: str | None = "0008_handover_pdfs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "medicine_schedules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "resident_id", sa.Uuid(), sa.ForeignKey("residents.id"), nullable=False
        ),
        sa.Column("prescribed_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("dose", sa.String(), nullable=True),
        sa.Column("instructions", sa.String(), nullable=True),
        sa.Column("frequency", sa.String(), nullable=False),
        # JSON column matches the model: `times_of_day` has a list default
        # in code so it's never NULL in practice, but the column itself is
        # nullable to match the pattern used by other JSON columns
        # (emergency_cases.symptom_codes, case_events.meta).
        sa.Column("times_of_day", sa.JSON(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        # No server_default: SQLModel fills the default at insert time. A
        # column-level boolean server_default trips alembic's SQLite add_constraint
        # path on create_table; the model default keeps the contract.
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_medicine_schedules_project_id", "medicine_schedules", ["project_id"]
    )
    op.create_index(
        "ix_medicine_schedules_resident_id", "medicine_schedules", ["resident_id"]
    )
    op.create_index(
        "ix_medicine_schedules_prescribed_by", "medicine_schedules", ["prescribed_by"]
    )
    op.create_index(
        "ix_medicine_schedules_frequency", "medicine_schedules", ["frequency"]
    )
    op.create_index(
        "ix_medicine_schedules_active", "medicine_schedules", ["active"]
    )

    op.create_table(
        "medicine_dose_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "schedule_id",
            sa.Uuid(),
            sa.ForeignKey("medicine_schedules.id"),
            nullable=False,
        ),
        sa.Column(
            "resident_id", sa.Uuid(), sa.ForeignKey("residents.id"), nullable=False
        ),
        sa.Column("scheduled_for", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("logged_at", sa.DateTime(), nullable=False),
        sa.Column("logged_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_medicine_dose_logs_project_id", "medicine_dose_logs", ["project_id"]
    )
    op.create_index(
        "ix_medicine_dose_logs_schedule_id", "medicine_dose_logs", ["schedule_id"]
    )
    op.create_index(
        "ix_medicine_dose_logs_resident_id", "medicine_dose_logs", ["resident_id"]
    )
    op.create_index(
        "ix_medicine_dose_logs_scheduled_for", "medicine_dose_logs", ["scheduled_for"]
    )
    op.create_index(
        "ix_medicine_dose_logs_status", "medicine_dose_logs", ["status"]
    )
    # One log per (schedule, scheduled_for slot) — the resident cannot
    # double-log the same dose. A retry that arrives twice (network flake)
    # collides on this constraint and the service maps it to the existing
    # log. Expressed as a UNIQUE INDEX (not a table-level constraint)
    # because SQLite cannot ALTER TABLE to add a constraint and `op.create_table`
    # routes table-level constraints through that path.
    op.create_index(
        "uq_medicine_dose_logs_schedule_slot",
        "medicine_dose_logs",
        ["schedule_id", "scheduled_for"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("medicine_dose_logs")
    op.drop_table("medicine_schedules")
