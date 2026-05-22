"""otp attempt ledger for auth rate limits

Revision ID: 0010_otp_attempts
Revises: 0009_medicine_reminders
Create Date: 2026-05-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_otp_attempts"
down_revision: str | None = "0009_medicine_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "otp_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("phone_fp", sa.String(), nullable=False),
        sa.Column("from_ip", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_otp_attempts_phone_fp", "otp_attempts", ["phone_fp"])
    op.create_index("ix_otp_attempts_from_ip", "otp_attempts", ["from_ip"])
    op.create_index("ix_otp_attempts_kind", "otp_attempts", ["kind"])
    op.create_index("ix_otp_attempts_attempted_at", "otp_attempts", ["attempted_at"])


def downgrade() -> None:
    op.drop_table("otp_attempts")
