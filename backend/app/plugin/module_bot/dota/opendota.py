"""OpenDota's public matches by rank: where the rank samples find a tier's ranked matches with a hero
(STRATZ can't search matches). Requests go through module_external's accounting (provider
"opendota"; the free API allows about 2000 a day per address, shared with the website)."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..movie.sources.base import SourceUnavailable, fetch, json_or_none

URL = "https://api.opendota.com/api/publicMatches"
RANKED = 7


async def public_matches(db: AsyncSession, tier: int, less_than: int | None = None) -> list[dict]:
    """Up to 100 public matches whose average rank is in the tier (medal 1–8; rank tiers are medal ×
    10 + stars), newest first, below the match id less_than when given. Ranked ones only."""
    params = {"min_rank": tier * 10, "max_rank": tier * 10 + 9}
    if less_than:
        params["less_than_match_id"] = less_than
    response = await fetch(db, "opendota", URL, params=params, timeout=30)
    body = json_or_none(response)
    if response.status_code != 200 or not isinstance(body, list):
        raise SourceUnavailable(f"OpenDota 回应错误（HTTP {response.status_code}）")
    return [m for m in body if m.get("lobby_type") == RANKED and m.get("match_id")]
