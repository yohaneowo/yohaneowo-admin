"""movie profiles: website accounts own them, Discord is optional

Revision ID: b7c1d2e3f4a5
Revises: 48f866302ff9
Create Date: 2026-10-17 15:00:00

bot_movie_subscriber was keyed by the Discord user; now a profile has its own id, an optional
Discord user and an optional website account (each unique), plus a `recommendations` switch for
the bot's daily DMs. Existing rows keep their Discord id and recommendations stay on for them.
bot_movie_record follows the profile id instead of the Discord id.
"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d2e3f4a5"
down_revision: str | None = "48f866302ff9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBSCRIBER = "bot_movie_subscriber"
RECORD = "bot_movie_record"


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column(SUBSCRIBER, sa.Column("profile_id", sa.String(length=36), nullable=True))
    op.add_column(SUBSCRIBER, sa.Column("site_user_id", sa.String(length=64), nullable=True))
    op.add_column(SUBSCRIBER, sa.Column("recommendations", sa.Boolean(), nullable=False, server_default=sa.false()))
    for (discord_id,) in bind.execute(sa.text(f"SELECT discord_id FROM {SUBSCRIBER}")).fetchall():
        bind.execute(sa.text(f"UPDATE {SUBSCRIBER} SET profile_id = :pid WHERE discord_id = :did"), {"pid": str(uuid4()), "did": discord_id})
    # Everyone who subscribed through the bot keeps getting recommendations.
    bind.execute(sa.text(f"UPDATE {SUBSCRIBER} SET recommendations = active"))
    op.alter_column(SUBSCRIBER, "profile_id", existing_type=sa.String(length=36), nullable=False)

    op.add_column(RECORD, sa.Column("profile_id", sa.String(length=36), nullable=True))
    bind.execute(sa.text(f"UPDATE {RECORD} SET profile_id = (SELECT s.profile_id FROM {SUBSCRIBER} s WHERE s.discord_id = {RECORD}.discord_id)"))
    bind.execute(sa.text(f"DELETE FROM {RECORD} WHERE profile_id IS NULL"))
    op.alter_column(RECORD, "profile_id", existing_type=sa.String(length=36), nullable=False)
    op.drop_constraint("PRIMARY", RECORD, type_="primary")
    op.create_primary_key(f"pk_{RECORD}", RECORD, ["profile_id", "tmdb_id"])
    op.drop_column(RECORD, "discord_id")

    op.drop_constraint("PRIMARY", SUBSCRIBER, type_="primary")
    op.create_primary_key(f"pk_{SUBSCRIBER}", SUBSCRIBER, ["profile_id"])
    op.alter_column(SUBSCRIBER, "discord_id", existing_type=sa.String(length=32), nullable=True)
    op.create_unique_constraint(f"uq_{SUBSCRIBER}_discord_id", SUBSCRIBER, ["discord_id"])
    op.create_unique_constraint(f"uq_{SUBSCRIBER}_site_user_id", SUBSCRIBER, ["site_user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    # Profiles without a Discord user cannot be expressed in the old schema; they are dropped.
    bind.execute(sa.text(f"DELETE FROM {RECORD} WHERE profile_id IN (SELECT profile_id FROM {SUBSCRIBER} WHERE discord_id IS NULL)"))
    bind.execute(sa.text(f"DELETE FROM {SUBSCRIBER} WHERE discord_id IS NULL"))

    op.add_column(RECORD, sa.Column("discord_id", sa.String(length=32), nullable=True))
    bind.execute(sa.text(f"UPDATE {RECORD} SET discord_id = (SELECT s.discord_id FROM {SUBSCRIBER} s WHERE s.profile_id = {RECORD}.profile_id)"))
    op.alter_column(RECORD, "discord_id", existing_type=sa.String(length=32), nullable=False)
    op.drop_constraint(f"pk_{RECORD}", RECORD, type_="primary")
    op.create_primary_key("PRIMARY", RECORD, ["discord_id", "tmdb_id"])
    op.drop_column(RECORD, "profile_id")

    op.drop_constraint(f"uq_{SUBSCRIBER}_site_user_id", SUBSCRIBER, type_="unique")
    op.drop_constraint(f"uq_{SUBSCRIBER}_discord_id", SUBSCRIBER, type_="unique")
    op.drop_constraint(f"pk_{SUBSCRIBER}", SUBSCRIBER, type_="primary")
    op.alter_column(SUBSCRIBER, "discord_id", existing_type=sa.String(length=32), nullable=False)
    op.create_primary_key("PRIMARY", SUBSCRIBER, ["discord_id"])
    op.drop_column(SUBSCRIBER, "recommendations")
    op.drop_column(SUBSCRIBER, "site_user_id")
    op.drop_column(SUBSCRIBER, "profile_id")
