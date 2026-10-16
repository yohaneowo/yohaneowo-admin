"""AniList (GraphQL, no key): English / romaji titles, staff with English names, studios, and the
MyAnimeList ID of each anime entry.

AniList allows about 30 requests a minute at the moment, so requests are serialized GAP_SECONDS
apart; a 429 waits for Retry-After and tries once more.
"""

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .base import SourceUnavailable, fetch, json_or_none

API_URL = "https://graphql.anilist.co"
GAP_SECONDS = 2.1
MAX_RETRY_WAIT_SECONDS = 65
_queue = asyncio.Lock()

MEDIA_FIELDS = """
  id idMal format episodes averageScore
  startDate { year month day }
  title { romaji english native }
  studios(isMain: true) { nodes { id name } }
  staff(perPage: 50, sort: [RELEVANCE, ID]) { edges { role node { id name { full native } } } }
  relations { edges { relationType node { id type format startDate { year month day } } } }
"""
SEARCH = f"""query ($search: String, $from: FuzzyDateInt, $to: FuzzyDateInt) {{
  Page(perPage: 10) {{ media(search: $search, type: ANIME, startDate_greater: $from, startDate_lesser: $to,
    format_in: [TV, TV_SHORT, ONA], sort: SEARCH_MATCH) {{ {MEDIA_FIELDS} }} }} }}"""
BY_ID = f"""query ($id: Int) {{ Media(id: $id, type: ANIME) {{ {MEDIA_FIELDS} }} }}"""


async def query(db: AsyncSession, text: str, variables: dict[str, Any]) -> Any:
    """The `data` of one GraphQL query."""
    async with _queue:
        for attempt in (1, 2):
            response = await fetch(db, "anilist", API_URL, method="POST", json={"query": text, "variables": variables}, headers={"Accept": "application/json"})
            await asyncio.sleep(GAP_SECONDS)
            if response.status_code == 429 and attempt == 1:
                await asyncio.sleep(min(int(response.headers.get("Retry-After") or 60), MAX_RETRY_WAIT_SECONDS))
                continue
            if response.status_code == 404:
                return None
            body = json_or_none(response)
            if not 200 <= response.status_code < 300 or not isinstance(body, dict):
                raise SourceUnavailable(f"AniList 回应 HTTP {response.status_code}")
            return body.get("data")
    raise SourceUnavailable("AniList 请求太频繁")


async def search(db: AsyncSession, title: str, date_from: int, date_to: int) -> list[dict]:
    """TV / ONA entries matching the title that started within (date_from, date_to), as YYYYMMDD."""
    data = await query(db, SEARCH, {"search": title, "from": date_from, "to": date_to})
    return ((data or {}).get("Page") or {}).get("media") or []


async def media(db: AsyncSession, anilist_id: int) -> dict | None:
    data = await query(db, BY_ID, {"id": anilist_id})
    return (data or {}).get("Media")
