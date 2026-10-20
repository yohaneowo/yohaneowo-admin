"""DotaHub: the other nine players of each stored ranked match (the same bracket, for comparing
against it): hero, side, position, lane, GPM, XPM, last hits, IMP, net worth and last hits per
minute, item timings

Revision ID: 86ca129cf034
Revises: 00d211d66443
Create Date: 2026-10-20 20:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "86ca129cf034"
down_revision: str | None = "00d211d66443"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bot_dota_match", sa.Column("others", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("bot_dota_match", "others")
