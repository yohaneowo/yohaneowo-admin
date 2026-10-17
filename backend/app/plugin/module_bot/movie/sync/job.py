"""The sync loop: claim a due profile, rebuild its snapshot and taste, store them.

Replaces the bot's standalone movie worker. The loop starts the first time a movie route is used
(ensure_loop is a dependency of those routers), like the other plugins' background work, so the
framework's startup stays untouched; the bot and the website call these routes all the time.
With several uvicorn workers each runs a loop; the claim lease keeps one sync per profile.
"""

import asyncio

from app.core.database import async_db_session
from app.core.logger import logger

from .. import service
from ..schema import Snapshot
from ..sources import trakt
from ..sources.base import SourceUnavailable
from .anime import enrich_anime
from .history import TraktTokenError, fetch_user_movies
from .shows import build_show_section
from .statistics import build_statistics, load_profile_extras
from .taste import build_taste, taste_json

IDLE_SECONDS = 15

_task: asyncio.Task | None = None
_wake = asyncio.Event()


async def compute(trakt_user: str, token: str | None = None) -> tuple[dict, dict, bool]:
    """(snapshot, taste, more): computed without storing anything; more = anime lookups were left
    for another sync. `token` is the profile owner's Trakt token (reads private accounts)."""
    history = await fetch_user_movies(trakt_user, token)
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


async def _token_for(profile_id: str) -> str | None:
    """The profile's Trakt access token, refreshed first when it is about to expire. None when the
    profile has no tokens (a public account read as the app). Raises TraktTokenError when the
    refresh is rejected, so the sync marks the profile as needing a new authorization."""
    async with async_db_session() as db, db.begin():
        row = await service.by_id(db, profile_id, lock=True)
        if row is None:
            return None
        tokens = service.tokens_of(row)
        if tokens is None:
            return None
        if not trakt.expiring(tokens):
            return tokens.access_token
        try:
            fresh = await trakt.refresh(db, tokens.refresh_token, row.trakt_redirect_uri or "")
        except trakt.TraktAuthError as error:
            raise TraktTokenError(str(error)) from error
        service.store_tokens(row, fresh, row.trakt_redirect_uri)
        return fresh.access_token


async def run_once() -> bool:
    """Sync one due profile. False when nothing was due."""
    async with async_db_session() as db, db.begin():
        job = await service.claim(db)
    if job is None:
        return False
    snapshot = taste = None
    more = False
    reauth = False
    try:
        token = await _token_for(job["profileId"])
        snapshot, taste, more = await compute(job["traktUser"], token)
    except TraktTokenError as error:
        reauth = True
        logger.warning(f"[movie-sync] Trakt token rejected for {job['profileId']}: {error}")
    except (SourceUnavailable, Exception) as error:  # noqa: BLE001 - any failure keeps the previous snapshot and retries later
        logger.warning(f"[movie-sync] Sync failed for {job['profileId']}: {error}")
    # The profile's name and avatar follow Trakt; a miss here changes nothing.
    profile = None
    if snapshot is not None:
        try:
            async with async_db_session() as db, db.begin():
                profile = await trakt.profile(db, job["traktUser"], token)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[movie-sync] Trakt profile lookup failed for {job['profileId']}: {error}")
    async with async_db_session() as db, db.begin():
        stored = await service.finish_sync(db, job["profileId"], job["revision"], job["leaseId"], snapshot, taste, again=more, reauth=reauth, profile=profile)
    if snapshot is not None and stored:
        logger.info(f"[movie-sync] Statistics snapshot saved for {job['profileId']}.")
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
