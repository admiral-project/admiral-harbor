"""base

Revision ID: 36fc52be0179
Revises:
Create Date: 2026-06-17 16:15:39.658929

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "36fc52be0179"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
