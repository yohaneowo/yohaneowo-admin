"""MyAnimeList scores by MAL ID. With MAL_CLIENT_ID set, the official API; otherwise Jikan (an
unofficial mirror, no key, about 3 requests a second). Requests are serialized: JIKAN_GAP_SECONDS
apart for Jikan, OFFICIAL_GAP_SECONDS for the official API (no published limit; kept polite)."""

import asyncio

from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import Settings

from .base import SourceUnavailable, fetch, json_or_none

OFFICIAL_URL = "https://api.myanimelist.net/v2/anime"
JIKAN_URL = "https://api.jikan.moe/v4/anime"
JIKAN_GAP_SECONDS = 1.1
OFFICIAL_GAP_SECONDS = 1.0
_queue = asyncio.Lock()


class MalSettings(BaseSettings):
    model_config = Settings.model_config
    MAL_CLIENT_ID: str = ""


async def score(db: AsyncSession, mal_id: int) -> dict | None:
    """{score, votes} (score None when nobody has rated it), or None when MAL doesn't know the ID."""
    client_id = MalSettings().MAL_CLIENT_ID
    async with _queue:
        if client_id:
            response = await fetch(db, "mal", f"{OFFICIAL_URL}/{mal_id}", params={"fields": "mean,num_scoring_users"}, headers={"X-MAL-CLIENT-ID": client_id})
        else:
            response = await fetch(db, "mal", f"{JIKAN_URL}/{mal_id}")
        await asyncio.sleep(OFFICIAL_GAP_SECONDS if client_id else JIKAN_GAP_SECONDS)
    if response.status_code == 404:
        return None
    body = json_or_none(response)
    if not 200 <= response.status_code < 300 or not isinstance(body, dict):
        raise SourceUnavailable(f"MyAnimeList 回应 HTTP {response.status_code}")
    if client_id:
        return {"score": body.get("mean"), "votes": body.get("num_scoring_users")}
    data = body.get("data") or {}
    return {"score": data.get("score"), "votes": data.get("scored_by")}
