"""Bonus picks: uses of a feature beyond its daily quota, per profile and feature. They start at
the feature's amount, are never reset, are spent only once the day's are used up, and the owner
adds (or takes back) some with the bot's /owner bonus. A feature with a bonus is listed in FEATURES."""

from sqlalchemy.ext.asyncio import AsyncSession

from .model import PickBonusModel

# feature → the bonus a profile starts with.
FEATURES = {"anime": 100}


async def left(db: AsyncSession, profile_id: str, feature: str) -> int:
    row = await db.get(PickBonusModel, (profile_id, feature))
    return max(0, row.amount) if row else FEATURES[feature]


async def add(db: AsyncSession, profile_id: str, feature: str, amount: int) -> int:
    """Adds (a negative amount takes back) bonus picks, never below 0; returns what is left."""
    row = await db.get(PickBonusModel, (profile_id, feature), with_for_update=True)
    if row is None:
        row = PickBonusModel(profile_id=profile_id, feature=feature, amount=FEATURES[feature])
        db.add(row)
    row.amount = max(0, row.amount + amount)
    await db.flush()
    return row.amount
