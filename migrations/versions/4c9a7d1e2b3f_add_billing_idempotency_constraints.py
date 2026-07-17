"""Add billing and rate-limit uniqueness constraints."""

from alembic import op


revision = "4c9a7d1e2b3f"
down_revision = "9f2d1f4c8a11"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_invoice_subscription_period",
        "invoice",
        ["subscription_external_id", "period_start"],
    )
    op.create_unique_constraint("uq_rate_limit_identifier", "rate_limit", ["identifier"])


def downgrade():
    op.drop_constraint("uq_rate_limit_identifier", "rate_limit", type_="unique")
    op.drop_constraint("uq_invoice_subscription_period", "invoice", type_="unique")
