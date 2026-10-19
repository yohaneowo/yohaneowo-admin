"""The DotaHub loop: every TICK seconds, check the linked accounts that are due (new ranked matches
for the DMs, and a page of history for the heat map). Started by the first DotaHub request, like the
movie sync; with several workers each runs a loop and SKIP LOCKED keeps one check per account."""

import asyncio

from sqlalchemy import or_, select

from app.core.database import async_db_session
from app.core.logger import logger

from ..movie.sources.base import SourceUnavailable
from . import service
from .model import DotaProfileModel

TICK = 30
PER_TICK = 5

_task: asyncio.Task | None = None


async def run_once() -> int:
    """Checks up to PER_TICK due accounts; returns how many."""
    now = service.utcnow()
    async with async_db_session() as db, db.begin():
        due = list(
            await db.scalars(
                select(DotaProfileModel)
                .where(or_(DotaProfileModel.next_check_at.is_(None), DotaProfileModel.next_check_at <= now))
                .order_by(DotaProfileModel.next_check_at)
                .limit(PER_TICK)
                .with_for_update(skip_locked=True)
            )
        )
        for profile in due:
            try:
                await service.check(db, profile)
            except (SourceUnavailable, Exception) as error:  # noqa: BLE001 - one account's failure must not stop the others
                profile.last_error = str(error)[:500]
                profile.next_check_at = now + service.RETRY
                logger.warning(f"[dota] check failed for {profile.account_id}: {error}")
    return len(due)


async def _loop() -> None:
    while True:
        try:
            await run_once()
        except Exception as error:  # noqa: BLE001 - the loop must survive database hiccups
            logger.warning(f"[dota] loop error: {error}")
        await asyncio.sleep(TICK)


async def ensure_loop() -> None:
    """Router dependency: start the loop in this process if it isn't running."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="dota-check")
        logger.info("[dota] loop started.")
