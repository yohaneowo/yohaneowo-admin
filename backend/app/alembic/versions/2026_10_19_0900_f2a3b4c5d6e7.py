"""anime picks: bot_anime_pick, per-profile anime quota and the airing switch

Revision ID: f2a3b4c5d6e7
Revises: 63ad8dd6ca6b
Create Date: 2026-10-19 09:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "63ad8dd6ca6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bot_movie_subscriber", sa.Column("anime_pick_quota", sa.Integer(), nullable=True))
    op.add_column("bot_movie_subscriber", sa.Column("anime_airing", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "bot_anime_pick",
        sa.Column("pick_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("anilist_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("day", sa.String(length=10), nullable=False),
        sa.Column("shown_at", sa.DateTime(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("own_rating", sa.Boolean(), nullable=False),
        sa.Column("history_id", sa.BigInteger(), nullable=True),
        sa.Column("history_added", sa.Boolean(), nullable=False),
        sa.Column("acted_at", sa.DateTime(), nullable=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("pick_id"),
    )
    op.create_index("ix_bot_anime_pick_profile_day", "bot_anime_pick", ["profile_id", "day"])
    op.create_index("ix_bot_anime_pick_profile_shown", "bot_anime_pick", ["profile_id", "shown_at"])


def downgrade() -> None:
    op.drop_index("ix_bot_anime_pick_profile_shown", table_name="bot_anime_pick")
    op.drop_index("ix_bot_anime_pick_profile_day", table_name="bot_anime_pick")
    op.drop_table("bot_anime_pick")
    op.drop_column("bot_movie_subscriber", "anime_airing")
    op.drop_column("bot_movie_subscriber", "anime_pick_quota")
