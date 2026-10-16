"""TMDB (themoviedb.org). TMDB_API_TOKEN takes the v4 read access token or the v3 API key."""

import re
from typing import Any

from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import Settings

from .base import SourceUnavailable, fetch, json_or_none

API_URL = "https://api.themoviedb.org/3"
# What the bot and the statistics read; anything else is refused by the proxy.
ALLOWED_PATH = re.compile(
    r"/(movie|tv)/\d+(/(reviews|credits|external_ids|movie_credits))?"
    r"|/movie/(now_playing|upcoming)"
    r"|/person/\d+(/(movie_credits|tv_credits))?"
    r"|/collection/\d+"
)


class TmdbSettings(BaseSettings):
    model_config = Settings.model_config
    TMDB_API_TOKEN: str = ""


def token() -> str:
    return TmdbSettings().TMDB_API_TOKEN


async def get(db: AsyncSession, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    """(HTTP status, JSON body) of one TMDB GET."""
    key = token()
    if not key:
        raise SourceUnavailable("admin 没有设定 TMDB_API_TOKEN")
    # The v4 read access token is a JWT; anything else is taken as the short v3 API key.
    bearer = key.startswith("eyJ")
    query = {**(params or {}), **({} if bearer else {"api_key": key})}
    headers = {"Accept": "application/json", **({"Authorization": f"Bearer {key}"} if bearer else {})}
    response = await fetch(db, "tmdb", f"{API_URL}{path}", params=query, headers=headers)
    return response.status_code, json_or_none(response)
