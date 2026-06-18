"""add user_session table

Revision ID: 20fc7d6a09c4
Revises: 36fc52be0179
Create Date: 2026-06-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20fc7d6a09c4"
down_revision: Union[str, Sequence[str], None] = "36fc52be0179"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("user_type", sa.String(length=20), nullable=False),
        sa.Column("user_identifier", sa.String(length=255), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.create_index(
        op.f("ix_user_session_session_id"),
        "user_session",
        ["session_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_session_session_id"), table_name="user_session")
    op.drop_table("user_session")
