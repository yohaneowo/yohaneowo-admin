"""DotaHub rank samples: a hero's ranked matches at a rank tier (medal 1–8), collected in the
background while the website compares players against them

Revision ID: ea1868d27c57
Revises: 86ca129cf034
Create Date: 2026-10-20 22:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ea1868d27c57"
down_revision: str | None = "86ca129cf034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_dota_sample_want",
        sa.Column("hero_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
        sa.Column("cursor", sa.BigInteger(), nullable=True),
        sa.Column("queue", sa.JSON(), nullable=True),
        sa.Column("seen", sa.JSON(), nullable=True),
        sa.Column("filled_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.PrimaryKeyConstraint("hero_id", "tier"),
    )
    op.create_table(
        "bot_dota_sample",
        sa.Column("hero_id", sa.Integer(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=False),
        sa.Column("match_id", sa.BigInteger(), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("hero_id", "tier", "match_id"),
    )
    op.create_index("ix_bot_dota_sample_start", "bot_dota_sample", ["hero_id", "tier", "start_time"])


def downgrade() -> None:
    op.drop_index("ix_bot_dota_sample_start", table_name="bot_dota_sample")
    op.drop_table("bot_dota_sample")
    op.drop_table("bot_dota_sample_want")
