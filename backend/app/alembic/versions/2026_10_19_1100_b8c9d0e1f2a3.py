"""bonus picks per profile and feature (bot_pick_bonus), moved out of bot_movie_subscriber

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-19 11:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_pick_bonus",
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("feature", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("profile_id", "feature"),
    )
    op.execute("INSERT INTO bot_pick_bonus (profile_id, feature, amount) SELECT profile_id, 'anime', anime_bonus_picks FROM bot_movie_subscriber")
    op.drop_column("bot_movie_subscriber", "anime_bonus_picks")


def downgrade() -> None:
    op.add_column("bot_movie_subscriber", sa.Column("anime_bonus_picks", sa.Integer(), nullable=False, server_default="100"))
    op.alter_column("bot_movie_subscriber", "anime_bonus_picks", existing_type=sa.Integer(), existing_nullable=False, server_default=None)
    op.execute("UPDATE bot_movie_subscriber s JOIN bot_pick_bonus b ON b.profile_id = s.profile_id AND b.feature = 'anime' SET s.anime_bonus_picks = b.amount")
    op.drop_table("bot_pick_bonus")
