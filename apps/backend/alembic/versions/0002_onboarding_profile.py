"""onboarding + profile: residents, emergency_contacts, consents,
medical_profiles

Revision ID: 0002_onboarding
Revises: 0001_core_auth
Create Date: 2026-05-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_onboarding"
down_revision: str | None = "0001_core_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "residents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False
        ),
        sa.Column("flat_villa_number", sa.String(), nullable=False),
        sa.Column("dob", sa.Date(), nullable=False),
        sa.Column("gender", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_residents_user_id", "residents", ["user_id"], unique=True)
    op.create_index("ix_residents_project_id", "residents", ["project_id"])

    op.create_table(
        "emergency_contacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "resident_id", sa.Uuid(), sa.ForeignKey("residents.id"), nullable=False
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=False),
        sa.Column("relation", sa.String(), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_emergency_contacts_resident_id", "emergency_contacts", ["resident_id"]
    )

    op.create_table(
        "consents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "resident_id", sa.Uuid(), sa.ForeignKey("residents.id"), nullable=False
        ),
        sa.Column("consent_type", sa.String(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("policy_version", sa.String(), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "resident_id", "consent_type", name="uq_consent_resident_type"
        ),
    )
    op.create_index("ix_consents_resident_id", "consents", ["resident_id"])
    op.create_index("ix_consents_consent_type", "consents", ["consent_type"])

    op.create_table(
        "medical_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "resident_id", sa.Uuid(), sa.ForeignKey("residents.id"), nullable=False
        ),
        sa.Column("blood_group", sa.String(), nullable=True),
        sa.Column("diseases", sa.JSON(), nullable=True),
        sa.Column("allergies", sa.JSON(), nullable=True),
        sa.Column("surgeries", sa.JSON(), nullable=True),
        sa.Column("preferred_hospital", sa.String(), nullable=True),
        sa.Column("insurance", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_medical_profiles_resident_id",
        "medical_profiles",
        ["resident_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("medical_profiles")
    op.drop_table("consents")
    op.drop_table("emergency_contacts")
    op.drop_table("residents")
