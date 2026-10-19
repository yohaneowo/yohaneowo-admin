"""The website's 推荐 page: one kind of recommendation (films from /movie recommend, anime from
/anime recommend) for a profile, read-only. Both kinds' picks come out in one shape, so the page
renders them the same way: today's picks (the daily quota's cards) and the last 30 days (today
included, newest first)."""

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from . import anime_recommend, recommend
from . import bonus as bonus_picks
from .model import AnimePickModel, MoviePickModel, MovieSubscriberModel
from .sync.common import is_integer

Kind = Literal["movie", "anime"]
RECENT_LIMIT = 60


def _movie_item(row: MoviePickModel) -> dict:
    card = row.data or {}
    return {
        "title": card.get("title") or row.title,
        "originalTitle": card.get("originalTitle"),
        "year": card.get("year") if is_integer(card.get("year")) else None,
        "poster": card.get("poster"),
        "people": [card["director"]] if card.get("director") else [],
        "genres": [g for g in card.get("genres") or [] if isinstance(g, str)][:3],
        "url": card.get("traktUrl") or f"https://www.themoviedb.org/movie/{row.tmdb_id}",
    }


def _anime_item(row: AnimePickModel) -> dict:
    card = row.data or {}
    links = card.get("links") or {}
    return {
        "title": card.get("title") or row.title,
        "originalTitle": card.get("titleNative") if card.get("titleNative") != card.get("title") else None,
        "year": card.get("year") if is_integer(card.get("year")) else None,
        "poster": card.get("cover"),
        "people": [s for s in card.get("studios") or [] if isinstance(s, str)][:2],
        "genres": [g for g in card.get("genres") or [] if isinstance(g, str)][:3],
        "url": links.get("bangumi") or links.get("anilist") or f"https://anilist.co/anime/{row.anilist_id}",
    }


def _out(row: MoviePickModel | AnimePickModel, kind: Kind) -> dict:
    return {
        "pickId": row.pick_id,
        "day": row.day,
        "shownAt": row.shown_at.isoformat() if row.shown_at else None,
        "mode": row.mode,
        "reason": row.reason,
        # None (undecided), skip, watchlist, watched, watched_existing, rated (补评分).
        "action": row.action,
        "rating": row.rating,
        "usesBonus": bool(row.uses_bonus),
        **(_movie_item(row) if kind == "movie" else _anime_item(row)),
    }


async def view(db: AsyncSession, row: MovieSubscriberModel, kind: Kind) -> dict:
    module = recommend if kind == "movie" else anime_recommend
    picks = sorted(await module._recent_picks(db, row.profile_id), key=lambda p: p.shown_at, reverse=True)  # noqa: SLF001
    today = recommend.today_taipei().isoformat()
    remaining = await (recommend._remaining(db, row) if kind == "movie" else anime_recommend.quota_left(db, row))  # noqa: SLF001
    return {
        "kind": kind,
        "today": today,
        "quota": module.quota_for(row),
        "remainingToday": remaining,
        "bonus": await bonus_picks.left(db, row.profile_id, kind),
        # The bot finds the profile by Discord ID: without one, picks can't be drawn yet.
        "discordLinked": bool(row.discord_id),
        "todayPicks": [_out(p, kind) for p in reversed(picks) if p.day == today],
        "recent": [_out(p, kind) for p in picks][:RECENT_LIMIT],
    }
