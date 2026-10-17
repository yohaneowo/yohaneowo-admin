"""bot_movie_pick: the bot's /movie recommend picks

Revision ID: d9e3f4a5b6c7
Revises: 912667000354
Create Date: 2026-10-18 09:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e3f4a5b6c7"
down_revision: str | None = "912667000354"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "bot_movie_pick"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("pick_id", sa.String(length=36), primary_key=True),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("tmdb_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("shown_at", sa.DateTime(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("acted_at", sa.DateTime(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_bot_movie_pick_profile_day", TABLE, ["profile_id", "day"])
    op.create_index("ix_bot_movie_pick_profile_shown", TABLE, ["profile_id", "shown_at"])


def downgrade() -> None:
    op.drop_index("ix_bot_movie_pick_profile_shown", table_name=TABLE)
    op.drop_index("ix_bot_movie_pick_profile_day", table_name=TABLE)
    op.drop_table(TABLE)
