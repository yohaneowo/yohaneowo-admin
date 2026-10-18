"""anime picks: bonus picks per profile (100 to start, never reset) and which picks used one

Revision ID: a7b8c9d0e1f2
Revises: 5b87368b0f85
Create Date: 2026-10-19 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "5b87368b0f85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows get their 100 through the server default, which then goes (the models keep
    # defaults on the Python side, so autogenerate sees no difference).
    op.add_column("bot_movie_subscriber", sa.Column("anime_bonus_picks", sa.Integer(), nullable=False, server_default="100"))
    op.alter_column("bot_movie_subscriber", "anime_bonus_picks", existing_type=sa.Integer(), existing_nullable=False, server_default=None)
    op.add_column("bot_anime_pick", sa.Column("uses_bonus", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("bot_anime_pick", "uses_bonus", existing_type=sa.Boolean(), existing_nullable=False, server_default=None)


def downgrade() -> None:
    op.drop_column("bot_anime_pick", "uses_bonus")
    op.drop_column("bot_movie_subscriber", "anime_bonus_picks")
