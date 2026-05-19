"""idempotency keys for resource-creating writes (brief §10)

Revision ID: 0003_idempotency
Revises: 0002_onboarding
Create Date: 2026-05-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_idempotency"
down_revision: str | None = "0002_onboarding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("owner_fp", sa.String(), nullable=False),
        sa.Column("request_fp", sa.String(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "endpoint", "key", name="uq_idempotency_endpoint_key"
        ),
    )
    op.create_index("ix_idempotency_keys_endpoint", "idempotency_keys", ["endpoint"])
    op.create_index("ix_idempotency_keys_key", "idempotency_keys", ["key"])


def downgrade() -> None:
    op.drop_table("idempotency_keys")
