"""DotaHub: read every linked player's ranked history again for the new details (team lead and win
chance per minute, lane outcome, the hero's average at 10 / 20 / 30 minutes)

Revision ID: 00d211d66443
Revises: 9ba984f331a9
Create Date: 2026-10-20 18:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "00d211d66443"
down_revision: str | None = "9ba984f331a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Data only: stored matches take the fuller details as their pages come back.
    op.execute("UPDATE bot_dota_profile SET backfill_skip = 0, backfill_done = 0")


def downgrade() -> None:
    pass
