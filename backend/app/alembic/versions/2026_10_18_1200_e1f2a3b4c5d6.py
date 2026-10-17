"""picks: own rating + history id (undo), per-profile pick quota

Revision ID: e1f2a3b4c5d6
Revises: d9e3f4a5b6c7
Create Date: 2026-10-18 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d9e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bot_movie_pick", sa.Column("own_rating", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("bot_movie_pick", sa.Column("history_id", sa.BigInteger(), nullable=True))
    op.add_column("bot_movie_subscriber", sa.Column("pick_quota", sa.Integer(), nullable=True))
    # Ratings written so far all came through the pick (补评分 picks as well).
    op.execute("UPDATE bot_movie_pick SET own_rating = 1 WHERE rating IS NOT NULL")


def downgrade() -> None:
    op.drop_column("bot_movie_subscriber", "pick_quota")
    op.drop_column("bot_movie_pick", "history_id")
    op.drop_column("bot_movie_pick", "own_rating")
