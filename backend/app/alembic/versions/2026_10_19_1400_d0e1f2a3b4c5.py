"""DotaHub: linked Steam accounts and their ranked matches' deaths (for the death maps)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-19 14:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_dota_profile",
        sa.Column("site_user_id", sa.String(length=64), nullable=False),
        sa.Column("steam_id", sa.String(length=20), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_id", sa.String(length=32), nullable=True),
        sa.Column("notify", sa.Boolean(), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
        sa.Column("backfill_skip", sa.Integer(), nullable=False),
        sa.Column("backfill_done", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("site_user_id"),
        sa.UniqueConstraint("steam_id"),
    )
    op.create_table(
        "bot_dota_match",
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("hero_id", sa.Integer(), nullable=False),
        sa.Column("won", sa.Boolean(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("kills", sa.Integer(), nullable=False),
        sa.Column("deaths", sa.Integer(), nullable=False),
        sa.Column("assists", sa.Integer(), nullable=False),
        sa.Column("imp", sa.Integer(), nullable=True),
        sa.Column("parsed", sa.Boolean(), nullable=False),
        sa.Column("death_events", sa.JSON(), nullable=False),
        sa.Column("notify_state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "match_id"),
    )
    op.create_index("ix_bot_dota_match_account_start", "bot_dota_match", ["account_id", "start_time"])
    op.create_index("ix_bot_dota_match_notify", "bot_dota_match", ["notify_state"])


def downgrade() -> None:
    op.drop_index("ix_bot_dota_match_notify", table_name="bot_dota_match")
    op.drop_index("ix_bot_dota_match_account_start", table_name="bot_dota_match")
    op.drop_table("bot_dota_match")
    op.drop_table("bot_dota_profile")
