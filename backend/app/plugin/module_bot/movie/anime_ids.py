"""AniList → TMDB (and so Trakt) for anime, from the community-maintained Fribb/anime-lists
mapping (anime-list-full.json: AniList / MAL / AniDB IDs with TMDB's TV show and season, or film).

/anime recommend picks AniList entries but writes to Trakt, which knows TMDB's shows and seasons.
The list (~7.5 MB, refreshed upstream about weekly) is downloaded at most every REFRESH_DAYS and
kept in the cache as small buckets of BUCKET AniList IDs each: {id: ["tv", show, season, offset]
or ["movie", film]}. offset: where the entry starts within TMDB's season (a split season's second
part starts after the first part's episodes). The list declares no licence, so it is only fetched
at run time, never committed.
"""

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.logger import logger

from .sync.common import is_integer, read_cache, write_cache

URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
NAMESPACE = "anime_tmdb_map"
META_KEY = "meta"
BUCKET = 500
REFRESH_DAYS = 7
# After a failed download, wait this long before trying again (the old buckets stay in use).
RETRY_SECONDS = 3600

_last_attempt = 0.0


def _bucket(anilist_id: int) -> str:
    return f"b{anilist_id // BUCKET}"


def compact(entries: list[dict]) -> dict[str, dict[str, list]]:
    """{bucket: {anilist id: target}} from the list's entries; entries without a TMDB target or
    an AniList ID are left out. A TV target without a season is kept as season None."""
    buckets: dict[str, dict[str, list]] = {}
    for entry in entries:
        anilist_id = entry.get("anilist_id")
        tmdb = entry.get("themoviedb_id")
        if not is_integer(anilist_id) or not isinstance(tmdb, dict):
            continue
        if is_integer(tmdb.get("movie")):
            target: list[Any] = ["movie", tmdb["movie"]]
        elif is_integer(tmdb.get("tv")):
            season = (entry.get("season") or {}).get("tmdb")
            offset = (entry.get("episode_offset") or {}).get("tmdb") if isinstance(entry.get("episode_offset"), dict) else None
            target = ["tv", tmdb["tv"], season if is_integer(season) else None, offset if is_integer(offset) else 0]
        else:
            continue
        buckets.setdefault(_bucket(anilist_id), {})[str(anilist_id)] = target
    return buckets


async def refresh(force: bool = False) -> bool:
    """Download the list when the cached copy is older than REFRESH_DAYS. True when it changed."""
    global _last_attempt
    meta = (await read_cache(NAMESPACE, [META_KEY])).get(META_KEY) or {}
    fresh_after = (datetime.now(UTC) - timedelta(days=REFRESH_DAYS)).isoformat()
    if not force and str(meta.get("fetchedAt", "")) >= fresh_after:
        return False
    if not force and time.monotonic() - _last_attempt < RETRY_SECONDS:
        return False
    _last_attempt = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(URL)
        response.raise_for_status()
        entries = response.json()
    except (httpx.HTTPError, ValueError) as error:
        logger.warning(f"[anime-ids] mapping download failed: {error}")
        return False
    if not isinstance(entries, list):
        return False
    buckets = compact(entries)
    await write_cache(NAMESPACE, buckets)
    await write_cache(NAMESPACE, {META_KEY: {"fetchedAt": datetime.now(UTC).isoformat(), "entries": sum(len(b) for b in buckets.values())}})
    logger.info(f"[anime-ids] mapping refreshed: {sum(len(b) for b in buckets.values())} AniList entries")
    return True


async def lookup(anilist_ids: list[int]) -> dict[int, dict]:
    """{AniList ID: {"type": "tv", "tmdbId", "season", "offset"} or {"type": "movie", "tmdbId"}} for
    the IDs the mapping knows."""
    await refresh()
    ids = [i for i in dict.fromkeys(anilist_ids) if is_integer(i)]
    buckets = await read_cache(NAMESPACE, sorted({_bucket(i) for i in ids}))
    out: dict[int, dict] = {}
    for anilist_id in ids:
        target = (buckets.get(_bucket(anilist_id)) or {}).get(str(anilist_id))
        if not target:
            continue
        if target[0] == "movie":
            out[anilist_id] = {"type": "movie", "tmdbId": target[1], "season": None, "offset": 0}
        else:
            out[anilist_id] = {"type": "tv", "tmdbId": target[1], "season": target[2], "offset": target[3] if len(target) > 3 else 0}
    return out
