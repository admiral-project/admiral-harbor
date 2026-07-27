"""add contractual fiscal snapshot fields

Revision ID: 9f2d1f4c8a11
Revises: c7d2e8f1a4b9
Create Date: 2026-06-23 22:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f2d1f4c8a11"
down_revision: str | Sequence[str] | None = "c7d2e8f1a4b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customer",
        sa.Column("fiscal_acceptance_country_code", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "customer",
        sa.Column("fiscal_acceptance_snapshot_json", sa.Text(), nullable=True),
    )
    op.add_column("customer", sa.Column("fiscal_accepted_at", sa.DateTime(), nullable=True))

    op.add_column(
        "subscription",
        sa.Column("tax_cents", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "subscription",
        sa.Column("fiscal_adjustment_cents", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "subscription",
        sa.Column("total_cents", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "subscription",
        sa.Column("fiscal_country_code", sa.String(length=2), nullable=True),
    )
    op.add_column("subscription", sa.Column("fiscal_snapshot_json", sa.Text(), nullable=True))

    op.add_column(
        "invoice",
        sa.Column("fiscal_adjustment_cents", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("invoice", sa.Column("fiscal_country_code", sa.String(length=2), nullable=True))
    op.add_column("invoice", sa.Column("fiscal_snapshot_json", sa.Text(), nullable=True))

    op.add_column("order", sa.Column("fiscal_country_code", sa.String(length=2), nullable=True))
    op.add_column("order", sa.Column("fiscal_snapshot_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("order", "fiscal_snapshot_json")
    op.drop_column("order", "fiscal_country_code")

    op.drop_column("invoice", "fiscal_snapshot_json")
    op.drop_column("invoice", "fiscal_country_code")
    op.drop_column("invoice", "fiscal_adjustment_cents")

    op.drop_column("subscription", "fiscal_snapshot_json")
    op.drop_column("subscription", "fiscal_country_code")
    op.drop_column("subscription", "total_cents")
    op.drop_column("subscription", "fiscal_adjustment_cents")
    op.drop_column("subscription", "tax_cents")

    op.drop_column("customer", "fiscal_accepted_at")
    op.drop_column("customer", "fiscal_acceptance_snapshot_json")
    op.drop_column("customer", "fiscal_acceptance_country_code")
