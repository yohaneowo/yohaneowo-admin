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


PLAYER_FIELDS = (
    "heroId isVictory kills deaths assists imp position lane award goldPerMinute experiencePerMinute networth"
    " numLastHits numDenies heroDamage stats { deathEvents { time positionX positionY attacker goldLost timeDead }"
    " networthPerMinute impPerMinute lastHitsPerMinute itemPurchases { time itemId } }"
)


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


async def retry_parse(db: AsyncSession, match_id: int) -> bool:
    """Asks STRATZ to fetch and parse the match again (the 重新提交解析 button on its site)."""
    data = await _query(db, f"mutation {{ retryMatchDownload(matchId: {int(match_id)}) }}")
    return bool(data.get("retryMatchDownload"))


def details(player: dict) -> dict | None:
    """The economy and IMP of one parsed match: position (1–5), lane, STRATZ's award, GPM, XPM, final
    net worth, last hits, denies, hero damage; per minute the net worth (running total), IMP (at that
    minute) and last hits (that minute's); each item's first purchase as [seconds, item id]."""
    stats = player.get("stats") or {}
    if not stats.get("networthPerMinute"):
        return None
    position = str(player.get("position") or "")
    first: dict[int, int] = {}
    for p in stats.get("itemPurchases") or []:
        item, time = p.get("itemId"), p.get("time")
        if item is not None and time is not None and item not in first:
            first[int(item)] = int(time)
    return {
        "pos": int(position[-1]) if position.startswith("POSITION_") and position[-1].isdigit() else None,
        "lane": player.get("lane"),
        "award": player.get("award") if player.get("award") not in (None, "NONE") else None,
        "gpm": int(player.get("goldPerMinute") or 0),
        "xpm": int(player.get("experiencePerMinute") or 0),
        "nw": int(player.get("networth") or 0),
        "lh": int(player.get("numLastHits") or 0),
        "dn": int(player.get("numDenies") or 0),
        "hd": int(player.get("heroDamage") or 0),
        "nwm": [int(v or 0) for v in stats.get("networthPerMinute") or []],
        "impm": [int(v or 0) for v in stats.get("impPerMinute") or []],
        "lhm": [int(v or 0) for v in stats.get("lastHitsPerMinute") or []],
        "items": sorted(([t, i] for i, t in first.items()), key=lambda p: p[0]),
    }


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
        "details": details(player) if match.get("parsedDateTime") else None,
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
