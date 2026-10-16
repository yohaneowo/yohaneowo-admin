"""A user's taste from their Trakt ratings and watches (port of the bot's taste.js buildTaste).

Two signals per genre: interest (how much of what they watch is in it) and affinity (how they rate
it relative to their own mean). The bot's recommendations read the result from admin; see taste_json.
"""

import asyncio
from typing import Any

from pydantic_settings import BaseSettings

from app.config.setting import Settings
from app.core.logger import logger

from .common import is_integer, is_number, js_round, js_str, read_cache, tmdb_get, write_cache
from .history import is_anime, tmdb_id

MAX_HISTORY_DETAILS = 200
# The website lists every movie (newest first) up to this many; details are cached, so only new
# ones cost a TMDB call.
MAX_TIMELINE = 3000
DETAIL_FETCH_CONCURRENCY = 4
# Watched without a rating: some interest, much weaker than a good rating.
WATCHED_WEIGHT = 0.3
MAX_HISTORY_SHOWS = 200
# 5–7: English titles and people's own names, for the English interface.
CACHE_VERSION = 7
PHOTO_BASE = "https://image.tmdb.org/t/p/w185"


class MovieSettings(BaseSettings):
    model_config = Settings.model_config
    MOVIE_LANGUAGE: str = "zh-TW"


def language() -> str:
    return MovieSettings().MOVIE_LANGUAGE or "zh-TW"


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


def english_title(details: dict, field: str) -> str | None:
    """The English title from TMDB's translations (append_to_response=translations). TMDB leaves it
    empty for English-language titles, whose original title is the English one."""
    for translation in (details.get("translations") or {}).get("translations") or []:
        title = (translation.get("data") or {}).get(field)
        if translation.get("iso_639_1") == "en" and title:
            return title
    if details.get("original_language") == "en":
        return details.get(f"original_{field}") or None
    return None


def slim_details(details: dict) -> dict:
    """Only what the taste profile and the statistics need, so the cache stays small. Same shape as
    the bot's slimDetails, so both read one cache."""
    release = details.get("release_date")
    countries = details.get("production_countries") or []
    vote_average = details.get("vote_average")
    vote_count = details.get("vote_count")
    poster = details.get("poster_path")
    crew = (details.get("credits") or {}).get("crew") or []
    cast = (details.get("credits") or {}).get("cast") or []
    return {
        "cacheVersion": CACHE_VERSION,
        "title": details.get("title"),
        "originalTitle": details.get("original_title"),
        "titleEn": english_title(details, "title"),
        "year": release[:4] if release is not None else None,
        "releaseDate": (release or "")[:10] or None,
        "genres": [genre.get("name") for genre in details.get("genres") or []],
        "genreIds": [genre.get("id") for genre in details.get("genres") or []],
        "country": (countries[0] or {}).get("name") if countries else None,
        "voteAverage": vote_average if is_number(vote_average) else None,
        "voteCount": vote_count if is_integer(vote_count) else None,
        "poster": f"{PHOTO_BASE}{poster}" if poster else None,
        # For the profile's 深度 axis: how much of the series the user has seen.
        "collectionId": (details.get("belongs_to_collection") or {}).get("id"),
        "directors": [_person(person) for person in crew if person.get("job") == "Director"],
        "cast": [_person(person) for person in cast[:5]],
    }


async def load_details(ids: list) -> dict[int, dict]:
    """{tmdb id: slim details}, from the shared cache; missing or outdated entries are fetched.
    A failed fetch keeps whatever (older) entry the cache had, as the bot did."""
    unique = list(dict.fromkeys(i for i in ids if i))
    cached = await read_cache("tmdb", [str(i) for i in unique])
    details = {i: cached[str(i)] for i in unique if str(i) in cached}
    missing = [i for i in unique if (cached.get(str(i)) or {}).get("cacheVersion") != CACHE_VERSION]
    lang = language()
    gate = asyncio.Semaphore(DETAIL_FETCH_CONCURRENCY)
    fresh: dict[str, dict] = {}

    async def one(movie_id: int) -> None:
        async with gate:
            try:
                status, body = await tmdb_get(
                    f"/movie/{movie_id}",
                    {
                        "language": lang,
                        "append_to_response": "credits,keywords,release_dates,videos,external_ids,translations",
                        "include_video_language": f"{lang.split('-')[0]},en,null",
                    },
                )
            except Exception as error:  # noqa: BLE001 - one movie failing must not stop the sync
                logger.warning(f"TMDB details for {movie_id} failed: {error}")
                return
            if not 200 <= status < 300 or not isinstance(body, dict):
                logger.warning(f"TMDB details for {movie_id} failed: HTTP {status}")
                return
            details[movie_id] = fresh[str(movie_id)] = slim_details(body)

    await asyncio.gather(*(one(i) for i in missing))
    await write_cache("tmdb", fresh)
    return details


async def build_taste(history: dict) -> tuple[dict, dict[int, dict]]:
    """(taste, details of every movie in taste["timeline"]). history: from fetch_user_movies."""
    ratings, watched = history["ratings"], history["watched"]
    show_ratings = history.get("showRatings") or []
    mean = sum(entry["rating"] for entry in ratings) / len(ratings) if ratings else 7

    # When a movie was seen: the last watch, or the rating date for rated movies with no watch
    # recorded. (Using the rating date for every rated movie dated old films rated in bulk as
    # watched that day.)
    watched_at = {tmdb_id(entry): entry.get("lastWatchedAt") for entry in watched if tmdb_id(entry)}
    timeline: dict[Any, dict] = {}
    for entry in ratings:
        timeline[tmdb_id(entry)] = {"rating": entry["rating"], "at": watched_at.get(tmdb_id(entry)) or entry.get("ratedAt")}
    for entry in watched:
        if tmdb_id(entry) not in timeline:
            timeline[tmdb_id(entry)] = {"rating": None, "at": entry.get("lastWatchedAt")}
    # Most recent first: tastes drift, and the latest history is what new releases compete with.
    ordered = sorted(((i, v) for i, v in timeline.items() if i), key=lambda item: js_str(item[1]["at"]), reverse=True)[:MAX_TIMELINE]
    sample = ordered[:MAX_HISTORY_DETAILS]
    details = await load_details([i for i, _ in ordered])

    genres: dict[Any, dict] = {}
    people: dict[Any, dict] = {}
    known = 0
    for movie_id, item in sample:
        movie = details.get(movie_id)
        if not movie:
            continue
        known += 1
        rating = item["rating"]
        weight = WATCHED_WEIGHT if rating is None else rating - mean
        for index, genre_id in enumerate(movie.get("genreIds") or []):
            genre = genres.get(genre_id) or {"name": movie["genres"][index], "count": 0, "weight": 0, "ratedCount": 0}
            genre["count"] += 1
            if rating is not None:
                genre["weight"] += weight
                genre["ratedCount"] += 1
            genres[genre_id] = genre
        for person, factor in [(p, 2) for p in movie.get("directors") or []] + [(p, 1) for p in movie.get("cast") or []]:
            entry = people.get(person["id"]) or {"name": person["name"], "weight": 0, "count": 0, "films": []}
            entry["weight"] += weight * factor
            entry["count"] += 1
            # The evidence shown next to a favourite's photo.
            if rating is not None and not any(film["id"] == movie_id for film in entry["films"]):
                entry["films"].append({"id": movie_id, "title": movie.get("title"), "rating": rating})
            people[person["id"]] = entry

    trakt_titles = {tmdb_id(entry): (entry.get("movie") or {}).get("title") for entry in [*ratings, *watched]}
    anime_ids = {tmdb_id(entry) for entry in [*ratings, *watched] if is_anime(entry.get("movie"))}
    plays_by_id = {tmdb_id(entry): entry.get("plays") or 0 for entry in watched}
    rated_shows = sorted(
        (entry for entry in show_ratings if ((entry.get("show") or {}).get("ids") or {}).get("tmdb")),
        key=lambda entry: js_str(entry.get("ratedAt")),
        reverse=True,
    )[:MAX_HISTORY_SHOWS]
    history_items = [
        {
            "type": "movie",
            "id": movie_id,
            "title": (details.get(movie_id) or {}).get("title") if (details.get(movie_id) or {}).get("title") is not None else trakt_titles.get(movie_id),
            "rating": item["rating"],
            "watchedAt": item["at"][:10] if isinstance(item["at"], str) else None,
        }
        for movie_id, item in sample
    ] + [{"type": "tv", "id": entry["show"]["ids"]["tmdb"], "title": entry["show"].get("title"), "rating": js_round(entry["rating"] * 10) / 10} for entry in rated_shows]

    timeline_items = [
        {
            "id": movie_id,
            "title": (details.get(movie_id) or {}).get("title") if (details.get(movie_id) or {}).get("title") is not None else trakt_titles.get(movie_id),
            "rating": item["rating"],
            "watchedAt": item["at"][:10] if isinstance(item["at"], str) else None,
            "anime": movie_id in anime_ids,
            # 0 for a rated movie never marked watched.
            "plays": plays_by_id.get(movie_id, 0),
        }
        for movie_id, item in ordered
    ]

    return {
        "mean": mean,
        # Every movie, newest first (the website's list); historyItems holds only the taste sample.
        "timeline": timeline_items,
        "showMean": sum(entry["rating"] for entry in show_ratings) / len(show_ratings) if show_ratings else None,
        "showRatingCount": len(show_ratings),
        "historyItems": history_items,
        "ratingCount": len(ratings),
        "watchedCount": len(watched),
        "knownCount": known,
        "seenIds": {i for i in (tmdb_id(entry) for entry in [*ratings, *watched]) if i},
        "genres": genres,
        "people": people,
        "ratings": ratings,
    }, details


def taste_json(taste: dict, details: dict[int, dict]) -> dict:
    """What the bot's recommendations read (services/movies/taste.js hydrateTaste rebuilds the Maps).
    Rating titles are resolved here so the bot needs no TMDB cache of its own."""

    def title(entry: dict) -> Any:
        cached = (details.get(tmdb_id(entry)) or {}).get("title")
        return cached if cached is not None else (entry.get("movie") or {}).get("title")

    return {
        "version": 1,
        "mean": taste["mean"],
        "showMean": taste["showMean"],
        "showRatingCount": taste["showRatingCount"],
        "ratingCount": taste["ratingCount"],
        "watchedCount": taste["watchedCount"],
        "knownCount": taste["knownCount"],
        "historyItems": taste["historyItems"],
        "seenIds": sorted(taste["seenIds"]),
        "ratings": [{"tmdb": tmdb_id(entry), "title": title(entry), "rating": entry["rating"]} for entry in taste["ratings"]],
        "genres": [[genre_id, genre] for genre_id, genre in taste["genres"].items()],
        "people": [[person_id, person] for person_id, person in taste["people"].items()],
    }
