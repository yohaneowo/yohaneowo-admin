"""The sync loop: claim a due subscription, rebuild its snapshot and taste, store them.

Replaces the bot's standalone movie worker. The loop starts the first time a movie route is used
(ensure_loop is a dependency of those routers), like the other plugins' background work, so the
framework's startup stays untouched; the bot and the website call these routes all the time.
With several uvicorn workers each runs a loop; the claim lease keeps one sync per subscription.
"""

import asyncio

from app.core.database import async_db_session
from app.core.logger import logger

from .. import service
from ..schema import Snapshot
from .anime import enrich_anime
from .history import fetch_user_movies
from .shows import build_show_section
from .statistics import build_statistics, load_profile_extras
from .taste import build_taste, taste_json

IDLE_SECONDS = 15

_task: asyncio.Task | None = None
_wake = asyncio.Event()


async def compute(trakt_user: str) -> tuple[dict, dict, bool]:
    """(snapshot, taste, more): computed without storing anything; more = anime lookups were left
    for another sync."""
    history = await fetch_user_movies(trakt_user)
    taste, details = await build_taste(history)
    movies = sum(1 for item in taste["historyItems"] if item["type"] == "movie")
    if taste["knownCount"] < movies:
        raise RuntimeError("Incomplete TMDB sample; retaining previous snapshot.")
    extras, shows, anime = await asyncio.gather(
        load_profile_extras(history, taste, details),
        build_show_section(history),
        build_show_section(history, anime=True),
    )
    more = await enrich_anime(anime)
    statistics = {**build_statistics(history, taste, details, extras), "shows": shows, "anime": anime}
    snapshot = Snapshot.model_validate(statistics).model_dump(mode="json")
    return snapshot, taste_json(taste, details), more


async def run_once() -> bool:
    """Sync one due subscription. False when nothing was due."""
    async with async_db_session() as db, db.begin():
        job = await service.claim(db)
    if job is None:
        return False
    snapshot = taste = None
    more = False
    try:
        snapshot, taste, more = await compute(job["traktUser"])
    except Exception as error:  # noqa: BLE001 - any failure keeps the previous snapshot and retries later
        logger.warning(f"[movie-sync] Sync failed for {job['userId']}: {error}")
    async with async_db_session() as db, db.begin():
        stored = await service.finish_sync(db, job["userId"], job["revision"], job["leaseId"], snapshot, taste, again=more)
    if snapshot is not None and stored:
        logger.info(f"[movie-sync] Statistics snapshot saved for {job['userId']}.")
    return True


async def _loop() -> None:
    while True:
        try:
            busy = await run_once()
        except Exception as error:  # noqa: BLE001 - the loop must survive database hiccups
            logger.warning(f"[movie-sync] Loop error: {error}")
            busy = False
        if not busy:
            _wake.clear()
            try:
                await asyncio.wait_for(_wake.wait(), IDLE_SECONDS)
            except TimeoutError:
                pass


def wake() -> None:
    """Check for due work soon (e.g. right after a refresh request) instead of at the next tick.
    Delayed a moment: the caller's transaction commits only after its response is sent."""
    asyncio.get_running_loop().call_later(1, _wake.set)


async def ensure_loop() -> None:
    """Router dependency: start the loop in this process if it isn't running."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(), name="movie-sync")
        logger.info("[movie-sync] Loop started.")
