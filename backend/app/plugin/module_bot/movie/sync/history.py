"""A Trakt user's watched movies and ratings (port of the bot's trakt.js fetchUserMovies). Public
profiles are read as the app; a connected account's token also reads a private one."""

import asyncio
from typing import Any
from urllib.parse import quote

from ..sources.base import SourceUnavailable
from .common import trakt_get

# Lists come back 100 items per page; follow the pages up to this many.
PAGE_LIMIT = 100
MAX_PAGES = 50


class TraktUserError(Exception):
    """The user is missing or private. The message is shown to them."""


class TraktTokenError(Exception):
    """The profile owner's token no longer works (expired, revoked): they must authorize again."""


def _part(value: str) -> str:
    # encodeURIComponent: these stay unescaped too.
    return quote(value, safe="!*'()")


async def _get(path: str, params: dict[str, Any] | None = None, token: str | None = None) -> tuple[int | None, Any]:
    status, page_count, body = await trakt_get(path, params, token)
    if status == 404:
        raise TraktUserError("找不到这个 Trakt 用户")
    if status == 401 and token:
        raise TraktTokenError("Trakt 授权已失效")
    if status in (401, 403):
        raise TraktUserError("这个 Trakt 帐号不是公开的")
    if not 200 <= status < 300:
        raise SourceUnavailable(f"Trakt {path} returned HTTP {status}")
    return page_count, body


async def _all(path: str, token: str | None = None) -> list:
    """Every item of a list, across pages; unpaginated lists (ratings) come in one go."""
    items: list = []
    for page in range(1, MAX_PAGES + 1):
        # extended=full adds each title's genres; "anime" there tells anime from other animation.
        page_count, body = await _get(path, {"page": page, "limit": PAGE_LIMIT, "extended": "full"}, token)
        items.extend(body or [])
        count = page_count or 1
        if count > MAX_PAGES:
            raise TraktUserError("观看记录超过当前同步上限，未保存不完整统计")
        if page >= count:
            break
    return items


def combine_show_ratings(season_ratings: list[dict], whole_show_ratings: list[dict]) -> list[dict]:
    """One rating per show: the average of its seasons, or the whole-show rating when no season is rated."""
    shows: dict[Any, dict] = {}
    for entry in season_ratings:
        key = entry["show"]["ids"]["trakt"]
        show = shows.get(key) or {"show": entry["show"], "ratings": [], "ratedAt": entry.get("rated_at")}
        show["ratings"].append(entry["rating"])
        if js_greater(entry.get("rated_at"), show["ratedAt"]):
            show["ratedAt"] = entry.get("rated_at")
        shows[key] = show
    for entry in whole_show_ratings:
        key = entry["show"]["ids"]["trakt"]
        if key not in shows:
            shows[key] = {"show": entry["show"], "ratings": [entry["rating"]], "ratedAt": entry.get("rated_at")}
    return [{"show": item["show"], "rating": sum(item["ratings"]) / len(item["ratings"]), "seasons": len(item["ratings"]), "ratedAt": item["ratedAt"]} for item in shows.values()]


def js_greater(a: Any, b: Any) -> bool:
    # rated_at values are ISO strings; a missing one never compares greater, as in JavaScript.
    return isinstance(a, str) and isinstance(b, str) and a > b


async def fetch_user_movies(slug: str, token: str | None = None) -> dict:
    """{profile, ratings: [{rating, ratedAt, movie}], watched: [{plays, lastWatchedAt, movie}],
    showRatings: [{rating, seasons, ratedAt, show}], watchedShows: [{plays, lastWatchedAt, show, episodes}]};
    movie / show: {title, year, ids: {trakt, slug, imdb, tmdb}}."""
    # The lists answer an empty 200 for users that don't exist, so check the profile first.
    _, profile = await _get(f"/users/{_part(slug)}", None, token)
    if (profile or {}).get("private") and not token:
        raise TraktUserError("这个 Trakt 帐号不是公开的")
    profile_slug = ((profile or {}).get("ids") or {}).get("slug") or slug
    user = _part(profile_slug)
    ratings, watched, season_ratings, whole_show_ratings, watched_shows = await asyncio.gather(
        _all(f"/users/{user}/ratings/movies", token),
        _all(f"/users/{user}/watched/movies", token),
        _all(f"/users/{user}/ratings/seasons", token),
        _all(f"/users/{user}/ratings/shows", token),
        _all(f"/users/{user}/watched/shows", token),
    )
    return {
        "profile": {"slug": profile_slug, "username": (profile or {}).get("username") or slug},
        "ratings": [{"rating": e.get("rating"), "ratedAt": e.get("rated_at"), "movie": e.get("movie") or {}} for e in ratings],
        "watched": [{"plays": e.get("plays"), "lastWatchedAt": e.get("last_watched_at"), "movie": e.get("movie") or {}} for e in watched],
        # Combined (the bot's taste uses it); the website keeps show and season ratings apart, as Trakt does.
        "showRatings": combine_show_ratings(season_ratings, whole_show_ratings),
        "wholeShowRatings": [{"rating": e.get("rating"), "ratedAt": e.get("rated_at"), "show": e.get("show") or {}} for e in whole_show_ratings],
        "seasonRatings": [{"rating": e.get("rating"), "ratedAt": e.get("rated_at"), "show": e.get("show") or {}, "season": (e.get("season") or {}).get("number")} for e in season_ratings],
        "watchedShows": [{"plays": e.get("plays"), "lastWatchedAt": e.get("last_watched_at"), "show": e.get("show") or {}, "episodes": watched_episodes(e)} for e in watched_shows],
    }


def watched_episodes(entry: dict) -> int:
    """Episodes seen. Trakt's watched list no longer carries seasons, so this is the show's episode
    plays (rewatches count again; the TV section caps it at the aired count). When seasons are
    present, the episodes seen at least once, specials (season 0) left out."""
    seasons = entry.get("seasons")
    if not isinstance(seasons, list):
        plays = entry.get("plays")
        return plays if isinstance(plays, int) and plays > 0 else 0
    return sum(
        1
        for season in seasons
        if isinstance(season, dict) and (season.get("number") or 0) >= 1
        for episode in season.get("episodes") or []
        if isinstance(episode, dict) and (episode.get("plays") or 0) > 0
    )


def is_anime(item: dict | None) -> bool:
    """Trakt tags Japanese-style animation "anime" (Western cartoons are only "animation")."""
    return "anime" in ((item or {}).get("genres") or [])


def tmdb_id(entry: dict) -> Any:
    return ((entry.get("movie") or {}).get("ids") or {}).get("tmdb")
