"""The website snapshot (port of the bot's statistics.js and depth.js).

Totals use the full Trakt history; preference evidence is limited to the taste sample.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import async_db_session
from app.core.logger import logger

from ..sources import omdb
from .common import is_integer, is_number, js_number, js_round, read_cache, tmdb_get, write_cache
from .history import tmdb_id

MAX_PROFILE_FILMS = 3000
MAX_RATED_IDS = 20000
IMDB_ID = re.compile(r"tt\d{5,12}")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
POSTER = re.compile(r"https://image\.tmdb\.org/t/p/w185/[A-Za-z0-9._-]+")

# 深度: directors count once two of their films are seen, series from the first.
DEPTH_CACHE_DAYS = 30
DEPTH_FETCH_CONCURRENCY = 4
MIN_DIRECTOR_FILMS = 2
MIN_SERIES_FILMS = 1


def profile_movies(taste: dict) -> list[dict]:
    """Every dated movie, newest first, shown one by one on the website."""
    return [item for item in taste["timeline"] if DATE.fullmatch(item.get("watchedAt") or "")][:MAX_PROFILE_FILMS]


def imdb_ids(history: dict) -> dict:
    ids = {}
    for entry in [*history["ratings"], *history["watched"]]:
        imdb = ((entry.get("movie") or {}).get("ids") or {}).get("imdb")
        if tmdb_id(entry) and isinstance(imdb, str) and IMDB_ID.fullmatch(imdb):
            ids[tmdb_id(entry)] = imdb
    return ids


# ── 深度 ──


def depth_of(seen_ids: set, directed: dict[str, list[int]], series: dict[str, list[int]]) -> dict:
    """Average share seen across the qualifying directors and series."""
    shares = []
    for lists, minimum in ((directed, MIN_DIRECTOR_FILMS), (series, MIN_SERIES_FILMS)):
        for ids in lists.values():
            seen = sum(1 for i in ids if i in seen_ids)
            if ids and seen >= minimum:
                shares.append(min(1, seen / len(ids)))
    return {"depth": sum(shares) / len(shares) if shares else None, "depthGroups": len(shares)}


def _released(date: Any, today: str) -> bool:
    return bool(date) and date <= today


async def _directed_ids(person_id: str) -> list[int]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    status, body = await tmdb_get(f"/person/{person_id}/movie_credits")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    ids = [c["id"] for c in (body or {}).get("crew") or [] if c.get("job") == "Director" and not c.get("video") and _released(c.get("release_date"), today)]
    return list(dict.fromkeys(ids))


async def _series_ids(collection_id: str) -> list[int]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    status, body = await tmdb_get(f"/collection/{collection_id}")
    if not 200 <= status < 300:
        raise RuntimeError(f"HTTP {status}")
    return [part["id"] for part in (body or {}).get("parts") or [] if _released(part.get("release_date"), today)]


async def _cached_lists(namespace: str, keys: list[str], fetch) -> dict[str, list[int]]:
    cached = await read_cache(namespace, keys)
    stale_before = datetime.now(UTC) - timedelta(days=DEPTH_CACHE_DAYS)
    lists: dict[str, list[int]] = {}
    missing = []
    for key in keys:
        entry = cached.get(key) or {}
        try:
            fetched_at = datetime.fromisoformat(str(entry.get("fetchedAt")).replace("Z", "+00:00"))
        except ValueError:
            fetched_at = None
        if isinstance(entry.get("ids"), list) and fetched_at and fetched_at > stale_before:
            lists[key] = entry["ids"]
        else:
            missing.append(key)
    gate = asyncio.Semaphore(DEPTH_FETCH_CONCURRENCY)
    fresh: dict[str, dict] = {}

    async def one(key: str) -> None:
        async with gate:
            try:
                ids = await fetch(key)
            except Exception as error:  # noqa: BLE001 - a missing filmography only leaves it out
                logger.warning(f"TMDB {namespace} {key} failed: {error}")
                return
            lists[key] = ids
            fresh[key] = {"ids": ids, "fetchedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")}

    await asyncio.gather(*(one(key) for key in missing))
    await write_cache(namespace, fresh)
    return lists


async def measure_depth(taste: dict, details: dict[int, dict]) -> dict:
    """Only directors seen twice in the sample are looked up (a few dozen TMDB calls); whether they
    qualify is then checked against everything seen."""
    director_counts: dict[Any, int] = {}
    series_ids: dict[Any, None] = {}
    for item in taste["historyItems"]:
        if item["type"] != "movie":
            continue
        movie = details.get(item["id"]) or {}
        for person in movie.get("directors") or []:
            director_counts[person["id"]] = director_counts.get(person["id"], 0) + 1
        if movie.get("collectionId"):
            series_ids[movie["collectionId"]] = None
    director_keys = [str(i) for i, count in director_counts.items() if count >= MIN_DIRECTOR_FILMS]
    directed, series = await asyncio.gather(
        _cached_lists("tmdb_person", director_keys, _directed_ids),
        _cached_lists("tmdb_collection", [str(i) for i in series_ids], _series_ids),
    )
    return depth_of(taste["seenIds"], directed, series)


# ── Snapshot ──


async def load_profile_extras(history: dict, taste: dict, details: dict[int, dict]) -> dict:
    """Public scores and 深度. Either may fail without blocking the snapshot."""
    by_tmdb = imdb_ids(history)
    wanted = [by_tmdb[item["id"]] for item in profile_movies(taste) if item["id"] in by_tmdb]

    async def ratings() -> dict:
        try:
            async with async_db_session() as db, db.begin():
                return await omdb.ratings(db, wanted)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"Public ratings unavailable: {error}")
            return {}

    async def depth() -> dict:
        try:
            return await measure_depth(taste, details)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"Depth unavailable: {error}")
            return {"depth": None, "depthGroups": 0}

    scores, depth_result = await asyncio.gather(ratings(), depth())
    return {"ratings": scores, "depth": depth_result}


def _score(value: Any, maximum: float) -> Any:
    return value if is_number(value) and 0 <= value <= maximum else None


def _text(value: Any, limit: int) -> str:
    return (value if isinstance(value, str) else str(value))[:limit]


def build_statistics(history: dict, taste: dict, details: dict[int, dict], extras: dict | None = None) -> dict:
    extras = extras or {}
    ratings = extras.get("ratings") or {}
    by_tmdb = imdb_ids(history)
    distribution = [0] * 10
    for entry in history["ratings"]:
        rating = entry.get("rating")
        if is_integer(rating) and 1 <= rating <= 10:
            distribution[int(rating) - 1] += 1

    people: dict[str, dict] = {}
    for film in (item for item in taste["historyItems"] if item["type"] == "movie" and item["rating"] is not None):
        movie = details.get(film["id"])
        if not movie:
            continue
        for role in ("director", "cast"):
            for person in movie.get("directors" if role == "director" else "cast") or []:
                key = f"{role}:{person['id']}"
                record = people.get(key) or {
                    "id": person["id"],
                    "name": person["name"],
                    "nameEn": person.get("originalName"),
                    "role": role,
                    "photo": person.get("photo"),
                    "films": [],
                }
                if not any(item["id"] == film["id"] for item in record["films"]):
                    record["films"].append({"id": film["id"], "title": movie.get("title"), "rating": film["rating"]})
                people[key] = record

    films = []
    for item in profile_movies(taste):
        movie = details.get(item["id"]) or {}
        year = js_number(movie.get("year"))
        poster = movie.get("poster")
        imdb_id = by_tmdb.get(item["id"])
        scores = (ratings.get(imdb_id) if imdb_id else None) or {}
        directors = movie.get("directors") or []
        first_director = directors[0] if directors else {}
        genres = movie.get("genres") or []
        votes = scores.get("imdbVotes")
        title = movie.get("title")
        if title is None:
            title = item.get("title") if item.get("title") is not None else "未命名"
        films.append(
            {
                "id": item["id"],
                "title": _text(title, 500),
                "originalTitle": _text(movie["originalTitle"], 500) if movie.get("originalTitle") else None,
                "titleEn": _text(movie["titleEn"], 500) if movie.get("titleEn") else None,
                "year": int(year) if is_integer(year) and 1880 <= year <= 2100 else None,
                "releaseDate": movie["releaseDate"] if DATE.fullmatch(movie.get("releaseDate") or "") else None,
                "director": _text(first_director["name"], 255) if first_director.get("name") else None,
                "directorId": first_director["id"] if is_integer(first_director.get("id")) and first_director["id"] > 0 else None,
                "directorEn": _text(first_director["originalName"], 255) if first_director.get("originalName") else None,
                "genre": _text(genres[0], 100) if genres and genres[0] else None,
                "genres": [_text(genre, 100) for genre in genres[:20]],
                "country": _text(movie["country"], 100) if movie.get("country") else None,
                "publicScore": js_round(movie["voteAverage"] * 10) / 10 if is_number(movie.get("voteAverage")) else None,
                "voteCount": movie["voteCount"] if is_integer(movie.get("voteCount")) and movie["voteCount"] >= 0 else None,
                "imdbId": imdb_id,
                "imdbRating": _score(scores.get("imdbRating"), 10),
                "imdbVotes": votes if is_integer(votes) and votes >= 0 else None,
                "rottenTomatoes": _score(scores.get("rottenTomatoes"), 100),
                "metacritic": _score(scores.get("metacritic"), 100),
                "myRating": item["rating"] if is_number(item["rating"]) else None,
                "watchedAt": item["watchedAt"],
                "poster": poster if isinstance(poster, str) and POSTER.fullmatch(poster) else None,
                "anime": bool(item.get("anime")),
                "plays": item.get("plays") or 0,
            }
        )

    favourites = []
    for person in people.values():
        count = len(person["films"])
        average = sum(film["rating"] for film in person["films"]) / count
        best = sorted(person["films"], key=lambda film: -film["rating"])[:3]
        if count >= 2 and average >= taste["mean"] + 0.3:
            favourites.append({**person, "count": count, "average": average, "films": best})

    known = max(taste["knownCount"], 1)
    genre_rows = [
        {
            "id": genre_id,
            "name": genre["name"],
            "count": genre["count"],
            "ratedCount": genre["ratedCount"],
            "interest": genre["count"] / known,
            "affinity": genre["weight"] / genre["ratedCount"] if genre["ratedCount"] else None,
            "average": taste["mean"] + genre["weight"] / genre["ratedCount"] if genre["ratedCount"] else None,
        }
        for genre_id, genre in taste["genres"].items()
    ]
    depth = extras.get("depth") or {}
    rated = [tmdb_id(entry) for entry in history["ratings"]]
    return {
        "version": 1,
        "ratingCount": len(history["ratings"]),
        "watchedCount": len(history["watched"]),
        "plays": sum(entry.get("plays") or 0 for entry in history["watched"]),
        "mean": taste["mean"] if history["ratings"] else None,
        "showRatingCount": taste["showRatingCount"],
        "showMean": taste["showMean"],
        "distribution": distribution,
        "sampleCount": taste["knownCount"],
        "sampleLimit": 200,
        "genres": sorted(genre_rows, key=lambda row: -row["count"]),
        "people": [person for role in ("director", "cast") for person in sorted((p for p in favourites if p["role"] == role), key=lambda p: (-p["average"], -p["count"]))[:8]],
        "films": films,
        # Every rated movie, for the ✓ next to a director's films on the website.
        "ratedIds": [i for i in dict.fromkeys(rated) if is_integer(i) and i > 0][:MAX_RATED_IDS],
        "depth": depth.get("depth"),
        "depthGroups": depth.get("depthGroups") or 0,
    }
