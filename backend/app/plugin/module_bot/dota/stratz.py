"""STRATZ's GraphQL API: a player's ranked matches with their death events (where on the map, when,
who killed them). Requests go through module_external's accounting (provider "stratz")."""

from datetime import UTC, datetime
from functools import lru_cache

from pydantic_settings import BaseSettings
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.setting import Settings

from ..movie.sources.base import SourceUnavailable, fetch, json_or_none

URL = "https://api.stratz.com/graphql"
RANKED = 7
PAGE = 100


class DotaSettings(BaseSettings):
    model_config = Settings.model_config
    STRATZ_API_TOKEN: str = ""


@lru_cache
def token() -> str:
    return DotaSettings().STRATZ_API_TOKEN


PLAYER_FIELDS = "heroId isVictory kills deaths assists imp stats { deathEvents { time positionX positionY attacker goldLost timeDead } }"


async def _query(db: AsyncSession, query: str) -> dict:
    if not token():
        raise SourceUnavailable("STRATZ 还没设定 STRATZ_API_TOKEN")
    # STRATZ asks API clients to send this exact user agent.
    response = await fetch(
        db,
        "stratz",
        URL,
        method="POST",
        json={"query": query},
        headers={"Authorization": f"Bearer {token()}", "User-Agent": "STRATZ_API"},
        timeout=40,
    )
    body = json_or_none(response) or {}
    if response.status_code != 200 or body.get("errors"):
        raise SourceUnavailable(f"STRATZ 回应错误（HTTP {response.status_code}）")
    return body.get("data") or {}


async def ranked_matches(db: AsyncSession, account_id: int, take: int, skip: int = 0) -> list[dict]:
    """The player's ranked matches, newest first, each with this player's line and deaths."""
    data = await _query(
        db,
        f"{{ player(steamAccountId: {int(account_id)}) {{ matches(request: {{ take: {int(take)}, skip: {int(skip)}, lobbyTypeIds: [{RANKED}] }}) {{"
        f" id startDateTime durationSeconds parsedDateTime firstBloodTime players(steamAccountId: {int(account_id)}) {{ {PLAYER_FIELDS} }} }} }} }}",
    )
    return ((data.get("player") or {}).get("matches")) or []


def to_row(account_id: int, match: dict) -> dict | None:
    """A match as bot_dota_match columns, or None without this player's line. Deaths keep STRATZ's
    map cells (x, y about 64–192; y grows to the north), seconds, killer hero, gold lost, seconds dead,
    and fb when that death was the match's first blood."""
    players = match.get("players") or []
    if not players or not match.get("id") or not match.get("startDateTime"):
        return None
    player = players[0]
    events = ((player.get("stats") or {}).get("deathEvents")) or []
    first_blood = match.get("firstBloodTime")
    return {
        "account_id": account_id,
        "match_id": int(match["id"]),
        "hero_id": int(player.get("heroId") or 0),
        "won": bool(player.get("isVictory")),
        "start_time": datetime.fromtimestamp(int(match["startDateTime"]), UTC).replace(tzinfo=None),
        "duration": int(match.get("durationSeconds") or 0),
        "kills": int(player.get("kills") or 0),
        "deaths": int(player.get("deaths") or 0),
        "assists": int(player.get("assists") or 0),
        "imp": int(player["imp"]) if isinstance(player.get("imp"), int) else None,
        "parsed": bool(match.get("parsedDateTime")),
        "death_events": [
            {
                "t": int(e.get("time") or 0),
                "x": int(e.get("positionX") or 0),
                "y": int(e.get("positionY") or 0),
                "by": int(e.get("attacker") or 0),
                "gold": int(e.get("goldLost") or 0),
                "dead": int(e.get("timeDead") or 0),
                "fb": first_blood is not None and e.get("time") == first_blood,
            }
            for e in events
            if e.get("positionX") is not None and e.get("positionY") is not None
        ],
    }
