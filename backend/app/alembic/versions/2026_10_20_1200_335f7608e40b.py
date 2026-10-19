"""DotaHub: read every linked player's ranked history again, to mark which deaths were first blood

Revision ID: 335f7608e40b
Revises: d0e1f2a3b4c5
Create Date: 2026-10-20 12:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "335f7608e40b"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Data only: the background check reads the history from the start again, and stored matches
    # take the fresh death events (now with the first-blood mark) as their pages come back.
    op.execute("UPDATE bot_dota_profile SET backfill_skip = 0, backfill_done = 0")


def downgrade() -> None:
    pass
