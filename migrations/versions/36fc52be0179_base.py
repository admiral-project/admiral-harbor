"""base

Revision ID: 36fc52be0179
Revises:
Create Date: 2026-06-17 16:15:39.658929

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "36fc52be0179"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""


def downgrade() -> None:
    """Downgrade schema."""
