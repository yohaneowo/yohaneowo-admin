"""movie picks: which ones used a bonus pick

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-19 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bot_movie_pick", sa.Column("uses_bonus", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("bot_movie_pick", "uses_bonus", existing_type=sa.Boolean(), existing_nullable=False, server_default=None)


def downgrade() -> None:
    op.drop_column("bot_movie_pick", "uses_bonus")
