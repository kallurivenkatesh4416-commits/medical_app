"""case lifecycle vitals notes

Revision ID: 0007_case_lifecycle
Revises: 0006_on_call_schedules
Create Date: 2026-05-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_case_lifecycle"
down_revision: str | None = "0006_on_call_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("emergency_cases", sa.Column("en_route_at", sa.DateTime(), nullable=True))
    op.add_column("emergency_cases", sa.Column("escalated_at", sa.DateTime(), nullable=True))

    op.create_table(
        "case_vitals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "case_id", sa.Uuid(), sa.ForeignKey("emergency_cases.id"), nullable=False
        ),
        sa.Column("recorded_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("blood_pressure_systolic", sa.Integer(), nullable=True),
        sa.Column("blood_pressure_diastolic", sa.Integer(), nullable=True),
        sa.Column("spo2_percent", sa.Integer(), nullable=True),
        sa.Column("heart_rate_bpm", sa.Integer(), nullable=True),
        sa.Column("respiratory_rate_bpm", sa.Integer(), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_case_vitals_project_id", "case_vitals", ["project_id"])
    op.create_index("ix_case_vitals_case_id", "case_vitals", ["case_id"])
    op.create_index("ix_case_vitals_recorded_by", "case_vitals", ["recorded_by"])

    op.create_table(
        "case_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "case_id", sa.Uuid(), sa.ForeignKey("emergency_cases.id"), nullable=False
        ),
        sa.Column("author_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("note_type", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("doctor_name", sa.String(), nullable=True),
        sa.Column("doctor_registration_number", sa.String(), nullable=True),
        sa.Column("consultation_timestamp", sa.DateTime(), nullable=True),
        sa.Column("advice_given", sa.String(), nullable=True),
        sa.Column("patient_consent_obtained", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_case_notes_project_id", "case_notes", ["project_id"])
    op.create_index("ix_case_notes_case_id", "case_notes", ["case_id"])
    op.create_index("ix_case_notes_author_id", "case_notes", ["author_id"])
    op.create_index("ix_case_notes_note_type", "case_notes", ["note_type"])


def downgrade() -> None:
    op.drop_table("case_notes")
    op.drop_table("case_vitals")
    op.drop_column("emergency_cases", "escalated_at")
    op.drop_column("emergency_cases", "en_route_at")
