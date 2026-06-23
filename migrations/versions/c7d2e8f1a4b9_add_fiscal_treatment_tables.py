"""add fiscal treatment tables

Revision ID: c7d2e8f1a4b9
Revises: 20fc7d6a09c4
Create Date: 2026-06-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7d2e8f1a4b9"
down_revision: Union[str, Sequence[str], None] = "20fc7d6a09c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fiscal_treatment_type",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("direction", sa.String(length=1), nullable=False),
        sa.Column("percent", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("is_optional", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("requires_evidence", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_fiscal_treatment_type_country_code"),
        "fiscal_treatment_type",
        ["country_code"],
        unique=False,
    )

    op.create_table(
        "customer_fiscal_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("customer_email", sa.String(length=255), nullable=False),
        sa.Column("treatment_type_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("evidence_path", sa.String(length=500), nullable=True),
        sa.Column("evidence_original_name", sa.String(length=255), nullable=True),
        sa.Column("request_notes", sa.Text(), nullable=True),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["treatment_type_id"], ["fiscal_treatment_type.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        op.f("ix_customer_fiscal_request_customer_email"),
        "customer_fiscal_request",
        ["customer_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_customer_fiscal_request_status"),
        "customer_fiscal_request",
        ["status"],
        unique=False,
    )

    op.add_column(
        "order",
        sa.Column("fiscal_adjustment_cents", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("order", "fiscal_adjustment_cents")
    op.drop_index(
        op.f("ix_customer_fiscal_request_status"),
        table_name="customer_fiscal_request",
    )
    op.drop_index(
        op.f("ix_customer_fiscal_request_customer_email"),
        table_name="customer_fiscal_request",
    )
    op.drop_table("customer_fiscal_request")
    op.drop_index(
        op.f("ix_fiscal_treatment_type_country_code"),
        table_name="fiscal_treatment_type",
    )
    op.drop_table("fiscal_treatment_type")
