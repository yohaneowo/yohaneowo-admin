"""DotaHub: each ranked match's economy and IMP details (position, GPM, per-minute net worth and IMP,
item timings), and reading the history again to fill them in

Revision ID: 9ba984f331a9
Revises: 335f7608e40b
Create Date: 2026-10-20 16:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9ba984f331a9"
down_revision: str | None = "335f7608e40b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("bot_dota_match", sa.Column("details", sa.JSON(), nullable=True))
    # The background check reads the history from the start again; stored matches take the details
    # as their pages come back.
    op.execute("UPDATE bot_dota_profile SET backfill_skip = 0, backfill_done = 0")


def downgrade() -> None:
    op.drop_column("bot_dota_match", "details")
