"""Public scores from OMDb (IMDb, Rotten Tomatoes, Metacritic), cached per IMDb ID.

Every answer (including "not found") is cached: RECENT_CACHE_DAYS for films from the last two years,
whose scores still move, OLD_CACHE_DAYS for older ones, so a long history doesn't spend the daily
quota re-reading settled scores. Only missing or stale IDs are fetched, in the order given (callers
pass newest first, so the films people look at first get their scores first). The daily quota comes
from 第三方接口 (module_external): when only part of the missing IDs fit, the rest wait for a later
sync. If OMDb itself refuses ("Request limit reached!", bad key), the provider is blocked for the
rest of the day. Quota and network errors are not cached.
"""

import asyncio
import re
from datetime import timedelta

import httpx
from pydantic_settings import BaseSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import Settings
from app.core.logger import logger
from app.plugin.module_external import service as usage

from ..model import MovieCacheModel
from ..service import utcnow

OMDB_URL = "https://www.omdbapi.com/"
NAMESPACE = "omdb"
RECENT_CACHE_DAYS = 7
OLD_CACHE_DAYS = 60
# Cache fields that are bookkeeping, not scores.
INTERNAL = {"fetchedAt", "year"}
CONCURRENCY = 4
TIMEOUT_SECONDS = 10


class OmdbSettings(BaseSettings):
    model_config = Settings.model_config
    OMDB_API_KEY: str = ""


def omdb_key() -> str:
    return OmdbSettings().OMDB_API_KEY


def _number(text: object, pattern: str) -> float | None:
    match = re.fullmatch(pattern, str(text or "").replace(",", ""))
    return float(match.group(1)) if match else None


def parse(body: dict) -> dict:
    """OMDb's strings ("7.8", "1,234", "87%", "74/100", "N/A") as numbers or None."""
    if body.get("Response") != "True":
        return {"imdbRating": None, "imdbVotes": None, "rottenTomatoes": None, "metacritic": None, "year": None}
    sources = {item.get("Source"): item.get("Value") for item in body.get("Ratings") or [] if isinstance(item, dict)}
    imdb = _number(body.get("imdbRating"), r"(\d+(?:\.\d+)?)")
    votes = _number(body.get("imdbVotes"), r"(\d+)")
    tomatoes = _number(sources.get("Rotten Tomatoes"), r"(\d+)%")
    metacritic = _number(sources.get("Metacritic"), r"(\d+)/100") or _number(body.get("Metascore"), r"(\d+)")
    return {
        "imdbRating": imdb if imdb is not None and 0 <= imdb <= 10 else None,
        "imdbVotes": int(votes) if votes is not None else None,
        "rottenTomatoes": int(tomatoes) if tomatoes is not None and tomatoes <= 100 else None,
        "metacritic": int(metacritic) if metacritic is not None and metacritic <= 100 else None,
        # Release year ("2019" or "2019–2021" for series), only to pick the cache lifetime.
        "year": int(year) if (year := _number(str(body.get("Year") or "")[:4], r"(\d{4})")) else None,
    }


async def _fetch(client: httpx.AsyncClient, imdb_id: str) -> tuple[dict | None, str | None, str | None]:
    """(scores, refusal, error): scores to cache, OMDb's refusal message, or a transient error."""
    try:
        response = await client.get(OMDB_URL, params={"i": imdb_id, "apikey": omdb_key()})
        if response.status_code == 401:
            body = response.json()
            return None, str(body.get("Error") or "HTTP 401"), None
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as error:
        return None, None, f"{type(error).__name__} ({imdb_id})"
    # Only the quota ("Request limit reached!") and a bad key stop OMDb for the day. Anything else
    # ("Movie not found!", "Error getting data." for some series) is about this one title: it is
    # cached as having no scores and looked at again when the cache expires.
    if body.get("Response") != "True" and is_refusal(str(body.get("Error", ""))):
        return None, str(body.get("Error")), None
    return parse(body), None, None


def is_refusal(error: str) -> bool:
    text = error.lower()
    return "limit" in text or "api key" in text


async def ratings(db: AsyncSession, imdb_ids: list[str]) -> dict[str, dict]:
    """{imdbId: {imdbRating, imdbVotes, rottenTomatoes, metacritic}}; IDs that could not be read are left out."""
    ids = list(dict.fromkeys(imdb_ids))
    rows = {row.cache_key: row for row in await db.scalars(select(MovieCacheModel).where(MovieCacheModel.namespace == NAMESPACE, MovieCacheModel.cache_key.in_(ids)))}
    now = utcnow()
    settled_year = now.year - 2

    def stale(data: dict) -> bool:
        year = data.get("year")
        days = OLD_CACHE_DAYS if isinstance(year, int) and year <= settled_year else RECENT_CACHE_DAYS
        return str(data.get("fetchedAt", "")) < (now - timedelta(days=days)).isoformat()

    result = {key: {k: v for k, v in row.data.items() if k not in INTERNAL} for key, row in rows.items()}
    missing = [key for key in ids if key not in rows or stale(rows[key].data)]
    await usage.record(db, "omdb", cache_hits=len(ids) - len(missing))
    if not missing or not omdb_key():
        return result
    left = await usage.remaining(db, "omdb")
    batch = missing if left is None else missing[:left]
    if len(batch) < len(missing):
        logger.warning(f"OMDb 今天只剩 {left} 次，{len(missing) - len(batch)} 部留到之后的同步再查")
    if not batch:
        return result

    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(client: httpx.AsyncClient, imdb_id: str):
        async with gate:
            return imdb_id, *await _fetch(client, imdb_id)

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        fetched = await asyncio.gather(*(one(client, imdb_id) for imdb_id in batch))
    fetched_at = utcnow().isoformat()
    refusals = [refusal for _, _, refusal, _ in fetched if refusal]
    errors = [error for _, _, _, error in fetched if error]
    for imdb_id, data, _, _ in fetched:
        if data is None:
            continue
        result[imdb_id] = {k: v for k, v in data.items() if k not in INTERNAL}
        row = rows.get(imdb_id)
        if row is None:
            db.add(MovieCacheModel(namespace=NAMESPACE, cache_key=imdb_id, data={**data, "fetchedAt": fetched_at}))
        else:
            row.data = {**data, "fetchedAt": fetched_at}
    problem = refusals[-1] if refusals else errors[-1] if errors else None
    await usage.record(db, "omdb", requests=len(batch), failures=len(refusals) + len(errors), error=problem)
    if refusals:
        await usage.block(db, "omdb", refusals[-1])
    await db.flush()
    return result
