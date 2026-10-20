"""add public staff replies to support tickets

Revision ID: b4ab1c3d4e5f
Revises: 4c9a7d1e2b3f
Create Date: 2026-10-20 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4ab1c3d4e5f"
down_revision: str | Sequence[str] | None = "4c9a7d1e2b3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_reply",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("staff_username", sa.String(length=255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_incident.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_staff_reply_ticket_id"), "staff_reply", ["ticket_id"], unique=False)
    op.create_index(op.f("ix_staff_reply_staff_username"), "staff_reply", ["staff_username"], unique=False)
    op.create_index(op.f("ix_staff_reply_created_at"), "staff_reply", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_staff_reply_created_at"), table_name="staff_reply")
    op.drop_index(op.f("ix_staff_reply_staff_username"), table_name="staff_reply")
    op.drop_index(op.f("ix_staff_reply_ticket_id"), table_name="staff_reply")
    op.drop_table("staff_reply")
