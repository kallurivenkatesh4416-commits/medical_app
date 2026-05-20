"""hospital handover pdfs and dispatches

Revision ID: 0008_handover_pdfs
Revises: 0007_case_lifecycle
Create Date: 2026-05-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_handover_pdfs"
down_revision: str | None = "0007_case_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handover_pdfs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "case_id", sa.Uuid(), sa.ForeignKey("emergency_cases.id"), nullable=False
        ),
        sa.Column("generated_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False, unique=True),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("doctor_name", sa.String(), nullable=False),
        sa.Column("doctor_registration_number", sa.String(), nullable=False),
        sa.Column("hospital_destination", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_handover_pdfs_project_id", "handover_pdfs", ["project_id"])
    op.create_index("ix_handover_pdfs_case_id", "handover_pdfs", ["case_id"])
    op.create_index("ix_handover_pdfs_generated_by", "handover_pdfs", ["generated_by"])
    op.create_index(
        "ix_handover_pdfs_storage_key", "handover_pdfs", ["storage_key"], unique=True
    )

    op.create_table(
        "handover_dispatches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "handover_id", sa.Uuid(), sa.ForeignKey("handover_pdfs.id"), nullable=False
        ),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("provider_ref", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_handover_dispatches_project_id", "handover_dispatches", ["project_id"]
    )
    op.create_index(
        "ix_handover_dispatches_handover_id", "handover_dispatches", ["handover_id"]
    )
    op.create_index(
        "ix_handover_dispatches_actor_user_id", "handover_dispatches", ["actor_user_id"]
    )
    op.create_index(
        "ix_handover_dispatches_channel", "handover_dispatches", ["channel"]
    )
    op.create_index(
        "ix_handover_dispatches_status", "handover_dispatches", ["status"]
    )


def downgrade() -> None:
    op.drop_table("handover_dispatches")
    op.drop_table("handover_pdfs")
