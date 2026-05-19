"""medical_records + idempotency_keys.resource_id

Revision ID: 0004_medical_records
Revises: 0003_idempotency
Create Date: 2026-05-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_medical_records"
down_revision: str | None = "0003_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_keys", sa.Column("resource_id", sa.Uuid(), nullable=True)
    )

    op.create_table(
        "medical_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "resident_id", sa.Uuid(), sa.ForeignKey("residents.id"), nullable=False
        ),
        sa.Column(
            "project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("record_type", sa.String(), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "uploaded_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_medical_records_resident_id", "medical_records", ["resident_id"]
    )
    op.create_index(
        "ix_medical_records_project_id", "medical_records", ["project_id"]
    )
    op.create_index(
        "ix_medical_records_record_type", "medical_records", ["record_type"]
    )
    op.create_index(
        "ix_medical_records_storage_key",
        "medical_records",
        ["storage_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("medical_records")
    op.drop_column("idempotency_keys", "resource_id")
