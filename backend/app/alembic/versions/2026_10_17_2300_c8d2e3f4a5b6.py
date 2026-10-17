"""movie profiles: Trakt OAuth tokens, avatar, one profile per Trakt account

Revision ID: c8d2e3f4a5b6
Revises: e745983e3661
Create Date: 2026-10-17 23:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d2e3f4a5b6"
down_revision: str | None = "e745983e3661"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBSCRIBER = "bot_movie_subscriber"


def upgrade() -> None:
    op.add_column(SUBSCRIBER, sa.Column("trakt_avatar", sa.String(length=500), nullable=True))
    op.add_column(SUBSCRIBER, sa.Column("trakt_private", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(SUBSCRIBER, sa.Column("trakt_access_token", sa.Text(), nullable=True))
    op.add_column(SUBSCRIBER, sa.Column("trakt_refresh_token", sa.Text(), nullable=True))
    op.add_column(SUBSCRIBER, sa.Column("trakt_token_expires_at", sa.DateTime(), nullable=True))
    op.add_column(SUBSCRIBER, sa.Column("trakt_redirect_uri", sa.String(length=500), nullable=True))
    op.add_column(SUBSCRIBER, sa.Column("trakt_reauth", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_unique_constraint(f"uq_{SUBSCRIBER}_trakt_user", SUBSCRIBER, ["trakt_user"])


def downgrade() -> None:
    op.drop_constraint(f"uq_{SUBSCRIBER}_trakt_user", SUBSCRIBER, type_="unique")
    for column in ("trakt_reauth", "trakt_redirect_uri", "trakt_token_expires_at", "trakt_refresh_token", "trakt_access_token", "trakt_private", "trakt_avatar"):
        op.drop_column(SUBSCRIBER, column)
