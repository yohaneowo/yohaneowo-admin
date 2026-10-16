"""The website's TV section: every show watched or rated, with progress and public scores.

Rows reuse the movie list's fields (ProfileFilm) so the website draws them the same way; for a show,
releaseDate is the first air date and director / directorId is the (first) creator. Ongoing shows'
TMDB details are refreshed weekly (new episodes change the aired count); ended ones stay cached.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import async_db_session
from app.core.logger import logger

from ..sources import omdb
from .common import is_integer, is_number, js_round, js_str, read_cache, tmdb_get, write_cache
from .history import is_anime
from .statistics import DATE, IMDB_ID, POSTER
from .taste import PHOTO_BASE, english_title, language

NAMESPACE = "tmdb_tv"
# 2: seasons (episode count, first air date, TMDB score) for the expandable rows.
# 3–5: original and English titles and people's own names (anime matching; the English interface).
CACHE_VERSION = 5
REFRESH_DAYS = 7
FINISHED = {"Ended", "Canceled"}
FETCH_CONCURRENCY = 4
MAX_SHOWS = 3000


def _person(person: dict) -> dict:
    path = person.get("profile_path")
    # name follows the request language (often a Chinese transliteration); original_name is the
    # person's own name, which the English interface shows.
    return {
        "id": person.get("id"),
        "name": person.get("name"),
        "originalName": person.get("original_name"),
        "photo": f"{PHOTO_BASE}{path}" if path else None,
    }


def aired_episodes(details: dict) -> int | None:
    """Regular episodes aired so far: whole seasons before the last aired one, plus its episodes."""
    last = details.get("last_episode_to_air")
    if not isinstance(last, dict) or not is_integer(last.get("season_number")) or not is_integer(last.get("episode_number")):
        return None
    if last["season_number"] < 1:
        return None
    earlier = sum(
        season.get("episode_count") or 0
        for season in details.get("seasons") or []
        if isinstance(season, dict) and is_integer(season.get("season_number")) and 1 <= season["season_number"] < last["season_number"]
    )
    return earlier + last["episode_number"]


def slim_show(details: dict) -> dict:
    first_air = details.get("first_air_date")
    countries = details.get("production_countries") or []
    vote_average = details.get("vote_average")
    vote_count = details.get("vote_count")
    poster = details.get("poster_path")
    cast = (details.get("credits") or {}).get("cast") or []
    return {
        "cacheVersion": CACHE_VERSION,
        "fetchedAt": datetime.now(UTC).isoformat(),
        "title": details.get("name"),
        "originalTitle": details.get("original_name"),
        "titleEn": english_title(details, "name"),
        "year": first_air[:4] if first_air else None,
        "firstAirDate": (first_air or "")[:10] or None,
        "genres": [genre.get("name") for genre in details.get("genres") or []],
        "genreIds": [genre.get("id") for genre in details.get("genres") or []],
        "country": (countries[0] or {}).get("name") if countries else None,
        "voteAverage": vote_average if is_number(vote_average) else None,
        "voteCount": vote_count if is_integer(vote_count) else None,
        "poster": f"{PHOTO_BASE}{poster}" if poster else None,
        "status": details.get("status"),
        "airedEpisodes": aired_episodes(details),
        "seasons": [
            {
                "number": season["season_number"],
                "name": season.get("name"),
                "episodes": season.get("episode_count") if is_integer(season.get("episode_count")) else None,
                "airDate": (season.get("air_date") or "")[:10] or None,
                # TMDB answers 0 for a season nobody voted on.
                "publicScore": js_round(season["vote_average"] * 10) / 10 if is_number(season.get("vote_average")) and season["vote_average"] > 0 else None,
            }
            for season in details.get("seasons") or []
            if isinstance(season, dict) and is_integer(season.get("season_number")) and season["season_number"] >= 1
        ],
        "creators": [_person(person) for person in details.get("created_by") or []],
        "cast": [_person(person) for person in cast[:5]],
    }


def _stale(entry: dict | None) -> bool:
    if not entry or entry.get("cacheVersion") != CACHE_VERSION:
        return True
    if entry.get("status") in FINISHED:
        return False
    return str(entry.get("fetchedAt", "")) < (datetime.now(UTC) - timedelta(days=REFRESH_DAYS)).isoformat()


async def load_show_details(ids: list) -> dict[int, dict]:
    """{tmdb id: slim show details}; a failed refresh keeps the cached entry."""
    unique = list(dict.fromkeys(i for i in ids if i))
    cached = await read_cache(NAMESPACE, [str(i) for i in unique])
    details = {i: cached[str(i)] for i in unique if str(i) in cached}
    missing = [i for i in unique if _stale(cached.get(str(i)))]
    lang = language()
    gate = asyncio.Semaphore(FETCH_CONCURRENCY)
    fresh: dict[str, dict] = {}

    async def one(show_id: int) -> None:
        async with gate:
            try:
                status, body = await tmdb_get(f"/tv/{show_id}", {"language": lang, "append_to_response": "credits,translations"})
            except Exception as error:  # noqa: BLE001 - one show failing must not stop the sync
                logger.warning(f"TMDB tv {show_id} failed: {error}")
                return
            if not 200 <= status < 300 or not isinstance(body, dict):
                logger.warning(f"TMDB tv {show_id} failed: HTTP {status}")
                return
            details[show_id] = fresh[str(show_id)] = slim_show(body)

    await asyncio.gather(*(one(i) for i in missing))
    await write_cache(NAMESPACE, fresh)
    return details


def _show_id(entry: dict) -> Any:
    return ((entry.get("show") or {}).get("ids") or {}).get("tmdb")


def _text(value: Any, limit: int) -> str:
    return (value if isinstance(value, str) else str(value))[:limit]


def _number_or_none(value: Any) -> Any:
    return value if is_number(value) else None


async def build_show_section(history: dict, anime: bool = False) -> dict | None:
    """The snapshot's `shows` (anime=False) or `anime` (anime series only); None when there are none."""

    def mine(entries: list) -> list:
        return [e for e in entries or [] if is_anime(e.get("show")) == anime]

    watched = mine(history.get("watchedShows"))
    # Shows rated in any way are listed; only the whole-show rating is "my score" (as on Trakt).
    rated = mine(history.get("showRatings"))
    if not watched and not rated:
        return None
    rating_by_id = {_show_id(e): e["rating"] for e in mine(history.get("wholeShowRatings")) if _show_id(e) and is_number(e.get("rating"))}
    season_ratings = {(_show_id(e), e["season"]): e["rating"] for e in mine(history.get("seasonRatings")) if _show_id(e) and is_integer(e.get("season")) and is_number(e.get("rating"))}
    shows: dict[Any, dict] = {}
    for entry in watched:
        if _show_id(entry):
            shows[_show_id(entry)] = {"show": entry["show"], "at": entry.get("lastWatchedAt"), "episodes": entry.get("episodes") or 0, "plays": entry.get("plays") or 0}
    for entry in rated:
        if _show_id(entry) and _show_id(entry) not in shows:
            shows[_show_id(entry)] = {"show": entry["show"], "at": entry.get("ratedAt"), "episodes": 0, "plays": 0}
    ordered = sorted(shows.items(), key=lambda item: js_str(item[1]["at"]), reverse=True)[:MAX_SHOWS]
    details = await load_show_details([i for i, _ in ordered])

    imdb_by_id = {}
    for show_id, item in ordered:
        imdb = (item["show"].get("ids") or {}).get("imdb")
        if isinstance(imdb, str) and IMDB_ID.fullmatch(imdb):
            imdb_by_id[show_id] = imdb
    scores: dict = {}
    if imdb_by_id:
        try:
            async with async_db_session() as db, db.begin():
                scores = await omdb.ratings(db, list(imdb_by_id.values()))
        except Exception as error:  # noqa: BLE001 - shows still list without public scores
            logger.warning(f"Show ratings unavailable: {error}")

    items = []
    genres: dict[Any, dict] = {}
    people: dict[str, dict] = {}
    mean = sum(rating_by_id.values()) / len(rating_by_id) if rating_by_id else None
    for show_id, item in ordered:
        show = details.get(show_id) or {}
        watched_at = item["at"][:10] if isinstance(item["at"], str) else None
        if not DATE.fullmatch(watched_at or ""):
            continue
        my_rating = rating_by_id.get(show_id)
        imdb_id = imdb_by_id.get(show_id)
        public = (scores.get(imdb_id) if imdb_id else None) or {}
        creators = show.get("creators") or []
        first_creator = creators[0] if creators else {}
        year = show.get("year")
        poster = show.get("poster")
        title = show.get("title") if show.get("title") is not None else item["show"].get("title") or "未命名"
        votes = public.get("imdbVotes")
        items.append(
            {
                "id": show_id,
                "title": _text(title, 500),
                "originalTitle": _text(show["originalTitle"], 500) if show.get("originalTitle") else None,
                "titleEn": _text(show["titleEn"], 500) if show.get("titleEn") else None,
                "year": int(year) if isinstance(year, str) and year.isdigit() and 1880 <= int(year) <= 2100 else None,
                "releaseDate": show["firstAirDate"] if DATE.fullmatch(show.get("firstAirDate") or "") else None,
                "director": _text(first_creator["name"], 255) if first_creator.get("name") else None,
                "directorId": first_creator["id"] if is_integer(first_creator.get("id")) and first_creator["id"] > 0 else None,
                "directorEn": _text(first_creator["originalName"], 255) if first_creator.get("originalName") else None,
                "genre": _text(show["genres"][0], 100) if show.get("genres") else None,
                "genres": [_text(genre, 100) for genre in (show.get("genres") or [])[:20]],
                "country": _text(show["country"], 100) if show.get("country") else None,
                "publicScore": js_round(show["voteAverage"] * 10) / 10 if is_number(show.get("voteAverage")) else None,
                "voteCount": show["voteCount"] if is_integer(show.get("voteCount")) and show["voteCount"] >= 0 else None,
                "imdbId": imdb_id,
                "imdbRating": _number_or_none(public.get("imdbRating")),
                "imdbVotes": votes if is_integer(votes) and votes >= 0 else None,
                "rottenTomatoes": _number_or_none(public.get("rottenTomatoes")),
                "metacritic": _number_or_none(public.get("metacritic")),
                "myRating": my_rating,
                "watchedAt": watched_at,
                "poster": poster if isinstance(poster, str) and POSTER.fullmatch(poster) else None,
                # Plays include rewatches, so never more than what has aired.
                "episodesWatched": min(item["episodes"], show["airedEpisodes"]) if is_integer(show.get("airedEpisodes")) else item["episodes"],
                "episodesAired": show.get("airedEpisodes"),
                "plays": item["plays"],
                "seasons": [{**season, "myRating": season_ratings.get((show_id, season["number"]))} for season in show.get("seasons") or []],
            }
        )
        for index, genre_id in enumerate(show.get("genreIds") or []):
            genre = genres.get(genre_id) or {"name": show["genres"][index], "count": 0, "weight": 0, "ratedCount": 0}
            genre["count"] += 1
            if my_rating is not None and mean is not None:
                genre["weight"] += my_rating - mean
                genre["ratedCount"] += 1
            genres[genre_id] = genre
        if my_rating is not None:
            for role, persons in (("creator", creators), ("cast", show.get("cast") or [])):
                for person in persons:
                    key = f"{role}:{person['id']}"
                    record = people.get(key) or {
                        "id": person["id"],
                        "name": person["name"],
                        "nameEn": person.get("originalName"),
                        "role": role,
                        "photo": person.get("photo"),
                        "films": [],
                    }
                    record["films"].append({"id": show_id, "title": _text(title, 500), "rating": my_rating})
                    people[key] = record

    favourites = []
    for person in people.values():
        count = len(person["films"])
        average = sum(f["rating"] for f in person["films"]) / count
        if mean is not None and count >= 2 and average >= mean + 0.3:
            favourites.append({**person, "count": count, "average": average, "films": sorted(person["films"], key=lambda f: -f["rating"])[:3]})
    distribution = [0] * 10
    for rating in rating_by_id.values():
        bucket = js_round(rating)
        if 1 <= bucket <= 10:
            distribution[bucket - 1] += 1
    total = max(len(items), 1)
    return {
        "watchedCount": len(watched),
        "episodes": sum(entry.get("episodes") or 0 for entry in watched),
        "ratingCount": len(rating_by_id),
        "mean": mean,
        "distribution": distribution,
        "genres": sorted(
            (
                {
                    "id": genre_id,
                    "name": genre["name"],
                    "count": genre["count"],
                    "ratedCount": genre["ratedCount"],
                    "interest": genre["count"] / total,
                    "affinity": genre["weight"] / genre["ratedCount"] if genre["ratedCount"] else None,
                    "average": mean + genre["weight"] / genre["ratedCount"] if genre["ratedCount"] and mean is not None else None,
                }
                for genre_id, genre in genres.items()
            ),
            key=lambda row: -row["count"],
        ),
        "people": [person for role in ("creator", "cast") for person in sorted((p for p in favourites if p["role"] == role), key=lambda p: (-p["average"], -p["count"]))[:8]],
        "items": items,
    }
