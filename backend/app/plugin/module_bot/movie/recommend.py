"""Random movie picks for the bot's `/movie recommend` (docs: movie-recommend-spec in the site's
docs). Three pools built from the person's Trakt account, each sampled with a temperature so the
pick is random but leans to the best fits:

- comfort: what they will most likely enjoy (Trakt's own recommendations, films related to ones
  they loved, more from their favourite directors);
- bridge: a genre they never watch, but by a director or actor they love;
- explore: the most-voted classics of genres and decades they never watch, plus the first film
  of a well-rated series they have not started.

The pools are built from the account's watched / rated / watchlist / hidden lists through the
owner's Trakt token (so a token is required), cached for a day or until Trakt's last_activities
says the lists changed. A pick is one row in bot_movie_pick: that is how the day's quota, the
30-day no-repeat rule and the mode weights (a tiny bandit on "想看" vs "换一部") are kept."""

import asyncio
import math
import random
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_db_session
from app.core.logger import logger

from . import service
from .model import MoviePickModel, MovieSubscriberModel
from .sources import trakt
from .sources.base import SourceUnavailable
from .sync.common import is_integer, is_number, read_cache, tmdb_get, write_cache
from .sync.history import TraktTokenError
from .sync.taste import language, load_details

TAIPEI = ZoneInfo("Asia/Taipei")
POOL_NAMESPACE = "recommend_pool"
GLOBAL_NAMESPACE = "recommend_global"
POOL_VERSION = 1
POOL_MAX_AGE = timedelta(hours=24)
GLOBAL_MAX_AGE = timedelta(days=30)
DISCOVER_MAX_AGE = timedelta(days=7)
POSTER_BASE = "https://image.tmdb.org/t/p/w342"
TMDB_GENRE_ANIMATION = 16

# Three picks a day (the first plus two "换一部"), none repeated within a month.
DAILY_PICKS = 3
NO_REPEAT_DAYS = 30

CONFIG: dict[str, Any] = {
    "defaultMeanRating": 7.0,
    "unratedWeight": 0.5,
    "rewatchWeight": 2.0,
    "dislikedThreshold": -1.5,
    "negativeHalfLifeYears": 2,
    "likedThreshold": 1.5,
    "comfortM": 1000,
    "exploreM": 2000,
    "globalMean": 6.5,
    "temperature": 0.15,
    "maxPerSeed": 2,
    "maxPerDirector": 2,
    "maxPerCollection": 1,
    "maxPerGenre": 2,
    "poolSize": 60,
    "blindSpotThreshold": 0.3,
    "fallbackBlindSpotCount": 4,
    "genreDiscover": {"voteCountGte": 3000, "voteAverageGte": 7.5, "pages": 3},
    "decadeDiscover": {"voteCountGte": 1500, "voteAverageGte": 7.3, "pages": 2},
    "collection": {"minAvg": 7.0, "minTotalVotes": 5000, "minFirstFilm": 7.0},
    "initialWeights": {"comfort": 0.5, "bridge": 0.25, "explore": 0.25},
    "coldStartMinWatched": 10,
    "bridgeMinPoolSize": 3,
    "relatedSeeds": 5,
    "directorsForRecall": 3,
    "bridgeDirectors": 5,
    "bridgeActors": 10,
}

MODES = ("comfort", "bridge", "explore")


class TraktRequired(Exception):
    """The profile has no Trakt token: the person must connect Trakt on the website first."""


class DailyLimit(Exception):
    """Today's picks are used up."""

    def __init__(self, reset_at: datetime) -> None:
        super().__init__("今天的推荐额度用完了")
        self.reset_at = reset_at


class NothingToPick(Exception):
    """Every pool is empty after the filters and exclusions."""


# ── Small helpers ───────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def today_taipei() -> date:
    return datetime.now(TAIPEI).date()


def next_midnight_taipei() -> datetime:
    tomorrow = datetime.combine(today_taipei() + timedelta(days=1), datetime.min.time(), TAIPEI)
    return tomorrow.astimezone(UTC).replace(tzinfo=None)


def _tmdb_id(movie: dict | None) -> int | None:
    value = ((movie or {}).get("ids") or {}).get("tmdb")
    return int(value) if is_integer(value) and value > 0 else None


def _year(value: Any) -> int | None:
    try:
        year = int(str(value)[:4])
    except (TypeError, ValueError):
        return None
    return year if 1880 <= year <= 2100 else None


def bayes(average: float | None, votes: int | None, m: int) -> float:
    """IMDb's weighted rating on a 0..1 scale: few votes pull a film toward the global mean."""
    if not is_number(average) or not is_integer(votes) or votes <= 0:
        return 0.0
    c = CONFIG["globalMean"]
    rating = (votes / (votes + m)) * average + (m / (votes + m)) * c
    return max(0.0, min(1.0, (rating - 5) / 4))


def _normalize(pref: dict[Any, float]) -> dict[Any, float]:
    norm = math.sqrt(sum(v * v for v in pref.values()))
    return {k: v / norm for k, v in pref.items()} if norm else {}


def _cosine(vector: dict[Any, float], pref: dict[Any, float]) -> float:
    if not vector or not pref:
        return 0.0
    dot = sum(vector.get(k, 0.0) * v for k, v in pref.items())
    left = math.sqrt(sum(v * v for v in vector.values()))
    return max(0.0, min(1.0, dot / left)) if left else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _softmax_pick(candidates: list[dict], temperature: float, rng: random.Random) -> dict:
    scores = [c["score"] / temperature for c in candidates]
    top = max(scores)
    weights = [math.exp(s - top) for s in scores]
    return rng.choices(candidates, weights=weights, k=1)[0]


# ── Trakt lists ─────────────────────────────────────────────────────────────────────────────


async def _trakt(method: str, path: str, token: str, params: dict | None = None, json: Any = None) -> tuple[int, Any]:
    async with async_db_session() as db, db.begin():
        status, _, body = await trakt.call(db, method, path, params=params, token=token, json=json)
    if status in (401, 403):
        raise TraktTokenError(f"Trakt answered HTTP {status} for {path}")
    return status, body


async def _trakt_all(path: str, token: str, params: dict | None = None) -> list:
    """Every page of a paginated list (100 per page, at most 50 pages)."""
    items: list = []
    for page in range(1, 51):
        status, body = await _trakt("GET", path, token, {**(params or {}), "page": page, "limit": 100})
        if status != 200 or not isinstance(body, list):
            break
        items.extend(body)
        if len(body) < 100:
            break
    return items


async def last_activities(token: str) -> dict:
    status, body = await _trakt("GET", "/sync/last_activities", token)
    movies = (body or {}).get("movies") if status == 200 and isinstance(body, dict) else None
    return {k: (movies or {}).get(k) for k in ("watched_at", "rated_at", "watchlisted_at", "hidden_at")}


# ── Profile (what the person likes) ──────────────────────────────────────────────────────────


def _weight(rating: float | None, plays: int | None, when: str | None, mean: float) -> float:
    if is_number(rating):
        w = rating - mean
    else:
        w = CONFIG["unratedWeight"]
    if is_integer(plays) and plays >= 2:
        w = max(w, CONFIG["rewatchWeight"])
    if w < 0 and when:
        try:
            years = (_now() - datetime.fromisoformat(when.replace("Z", "+00:00"))).days / 365.25
            w *= 0.5 ** (years / CONFIG["negativeHalfLifeYears"])
        except ValueError:
            pass
    return w


async def build_profile(token: str) -> dict:
    """The person's lists and preference vectors."""
    watched, ratings, watchlist, hidden = await asyncio.gather(
        _trakt_all("/sync/watched/movies", token),
        _trakt_all("/sync/ratings/movies", token),
        _trakt_all("/sync/watchlist/movies", token),
        _trakt_all("/users/hidden/recommendations", token, {"type": "movie"}),
    )
    rating_by_id = {tid: e.get("rating") for e in ratings if (tid := _tmdb_id(e.get("movie"))) and is_number(e.get("rating"))}
    rated_at = {tid: e.get("rated_at") for e in ratings if (tid := _tmdb_id(e.get("movie")))}
    values = list(rating_by_id.values())
    mean = sum(values) / len(values) if len(values) >= 5 else CONFIG["defaultMeanRating"]
    watched_ids = [tid for e in watched if (tid := _tmdb_id(e.get("movie")))]
    trakt_ids = {tid: ((e.get("movie") or {}).get("ids") or {}).get("trakt") for e in watched if (tid := _tmdb_id(e.get("movie")))}
    titles = {tid: (e.get("movie") or {}).get("title") for e in watched if (tid := _tmdb_id(e.get("movie")))}
    plays = {tid: e.get("plays") for e in watched if (tid := _tmdb_id(e.get("movie")))}
    last_watched = {tid: e.get("last_watched_at") for e in watched if (tid := _tmdb_id(e.get("movie")))}
    weights = {tid: _weight(rating_by_id.get(tid), plays.get(tid), rated_at.get(tid) or last_watched.get(tid), mean) for tid in watched_ids}
    for tid, rating in rating_by_id.items():
        if tid not in weights:
            weights[tid] = _weight(rating, None, rated_at.get(tid), mean)
    details = await load_details(list(weights))
    genre_pref: dict[int, float] = {}
    genre_names: dict[int, str] = {}
    director_pref: dict[int, float] = {}
    actor_pref: dict[int, float] = {}
    people: dict[int, dict] = {}
    decade_pref: dict[int, float] = {}
    user_genre_count: dict[int, int] = {}
    for tid, w in weights.items():
        d = details.get(tid) or {}
        for gid, name in zip(d.get("genreIds") or [], d.get("genres") or [], strict=False):
            if is_integer(gid):
                genre_pref[gid] = genre_pref.get(gid, 0.0) + w
                genre_names[gid] = name
                user_genre_count[gid] = user_genre_count.get(gid, 0) + 1
        if w >= CONFIG["likedThreshold"] or (is_integer(plays.get(tid)) and plays[tid] >= 2):
            for person in d.get("directors") or []:
                if is_integer(person.get("id")):
                    director_pref[person["id"]] = director_pref.get(person["id"], 0.0) + w
                    people[person["id"]] = person
            for person in (d.get("cast") or [])[:5]:
                if is_integer(person.get("id")):
                    actor_pref[person["id"]] = actor_pref.get(person["id"], 0.0) + w
                    people[person["id"]] = person
        year = _year(d.get("year"))
        if year:
            decade_pref[year // 10 * 10] = decade_pref.get(year // 10 * 10, 0.0) + w
    disliked = {tid for tid, w in weights.items() if w <= CONFIG["dislikedThreshold"]}
    return {
        "mean": mean,
        "watched": watched_ids,
        "traktIds": trakt_ids,
        "titles": titles,
        "ratings": rating_by_id,
        "weights": weights,
        "watchlist": [tid for e in watchlist if (tid := _tmdb_id(e.get("movie")))],
        "watchlistedAt": {tid: e.get("listed_at") for e in watchlist if (tid := _tmdb_id(e.get("movie")))},
        "hidden": [tid for e in hidden if (tid := _tmdb_id(e.get("movie")))],
        "disliked": sorted(disliked),
        "dislikedCollections": sorted({cid for tid in disliked if is_integer(cid := (details.get(tid) or {}).get("collectionId"))}),
        "genrePref": _normalize(genre_pref),
        "genreNames": genre_names,
        "userGenreCount": user_genre_count,
        "directorPref": _normalize(director_pref),
        "actorPref": _normalize(actor_pref),
        "people": people,
        "decadePref": _normalize(decade_pref),
        "details": details,
    }


# ── Global TMDB facts (shared by everyone) ───────────────────────────────────────────────────


async def _global(key: str, max_age: timedelta, build) -> Any:
    cached = (await read_cache(GLOBAL_NAMESPACE, [key])).get(key)
    if cached and str(cached.get("fetchedAt", "")) >= (_now() - max_age).isoformat():
        return cached["data"]
    data = await build()
    await write_cache(GLOBAL_NAMESPACE, {key: {"fetchedAt": _now().isoformat(), "data": data}})
    return data


async def _discover(params: dict, pages: int) -> list[dict]:
    """TMDB /discover/movie results (released titles only), several pages."""
    results: list[dict] = []
    today = today_taipei().isoformat()
    for page in range(1, pages + 1):
        status, body = await tmdb_get("/discover/movie", {**params, "page": page, "language": language(), "include_adult": "false", "primary_release_date.lte": today})
        if status != 200 or not isinstance(body, dict):
            break
        results.extend(r for r in body.get("results") or [] if is_integer(r.get("id")))
        if page >= (body.get("total_pages") or 1):
            break
    return results


async def global_genre_share() -> dict[str, float]:
    """Share of each TMDB genre among widely-voted films, so a person's genre share can be
    compared with the base rate (westerns are rare for everyone)."""

    async def build() -> dict[str, float]:
        films = await _discover({"sort_by": "popularity.desc", "vote_count.gte": 1000}, pages=10)
        count: dict[int, int] = {}
        for film in films:
            for gid in film.get("genre_ids") or []:
                count[gid] = count.get(gid, 0) + 1
        total = max(len(films), 1)
        return {str(gid): n / total for gid, n in count.items()}

    return await _global("genre_share", GLOBAL_MAX_AGE, build)


async def genre_classics(genre_id: int) -> list[dict]:
    cfg = CONFIG["genreDiscover"]
    return await _global(
        f"genre:{genre_id}",
        DISCOVER_MAX_AGE,
        lambda: _discover({"with_genres": genre_id, "vote_count.gte": cfg["voteCountGte"], "vote_average.gte": cfg["voteAverageGte"], "sort_by": "vote_count.desc"}, cfg["pages"]),
    )


async def decade_classics(decade: int) -> list[dict]:
    cfg = CONFIG["decadeDiscover"]
    return await _global(
        f"decade:{decade}",
        DISCOVER_MAX_AGE,
        lambda: _discover(
            {
                "primary_release_date.gte": f"{decade}-01-01",
                "primary_release_date.lte": f"{decade + 9}-12-31",
                "vote_count.gte": cfg["voteCountGte"],
                "vote_average.gte": cfg["voteAverageGte"],
                "sort_by": "vote_count.desc",
            },
            cfg["pages"],
        ),
    )


async def person_credits(person_id: int) -> dict:
    """{cast: [...], crew: [...]} of a TMDB person, cached a week."""

    async def build() -> dict:
        status, body = await tmdb_get(f"/person/{person_id}/movie_credits", {"language": language()})
        if status != 200 or not isinstance(body, dict):
            return {"cast": [], "crew": []}

        def slim(m: dict) -> dict:
            return {
                "id": m.get("id"),
                "title": m.get("title"),
                "release_date": m.get("release_date"),
                "genre_ids": m.get("genre_ids") or [],
                "vote_average": m.get("vote_average"),
                "vote_count": m.get("vote_count"),
                "job": m.get("job"),
            }

        return {
            "cast": [slim(m) for m in body.get("cast") or [] if is_integer(m.get("id"))],
            "crew": [slim(m) for m in body.get("crew") or [] if is_integer(m.get("id")) and m.get("job") == "Director"],
        }

    return await _global(f"person:{person_id}", DISCOVER_MAX_AGE, build)


async def collection_parts(collection_id: int) -> list[dict]:
    async def build() -> list[dict]:
        status, body = await tmdb_get(f"/collection/{collection_id}", {"language": language()})
        if status != 200 or not isinstance(body, dict):
            return []
        parts = [p for p in body.get("parts") or [] if is_integer(p.get("id")) and p.get("release_date")]
        return [
            {"id": p["id"], "title": p.get("title"), "release_date": p["release_date"], "vote_average": p.get("vote_average"), "vote_count": p.get("vote_count"), "name": body.get("name")}
            for p in sorted(parts, key=lambda p: p["release_date"])
        ]

    return await _global(f"collection:{collection_id}", GLOBAL_MAX_AGE, build)


# ── Blind spots ──────────────────────────────────────────────────────────────────────────────


def blind_genres(profile: dict, share: dict[str, float]) -> dict[int, float]:
    """{genre id: blind-spot score} for the genres the person watches far less than the base rate
    (score = their share / everyone's share; below the threshold, or the lowest few)."""
    total = max(len(profile["watched"]), 1)
    scores: dict[int, float] = {}
    for gid_text, base in share.items():
        gid = int(gid_text)
        if gid == TMDB_GENRE_ANIMATION or base <= 0:
            continue
        scores[gid] = (profile["userGenreCount"].get(gid, 0) / total) / base
    blind = {gid: s for gid, s in scores.items() if s < CONFIG["blindSpotThreshold"]}
    if not blind:
        blind = dict(sorted(scores.items(), key=lambda kv: kv[1])[: CONFIG["fallbackBlindSpotCount"]])
    return blind


def blind_decades(profile: dict) -> dict[int, float]:
    """Decades from the 1950s on that the person has (almost) never watched."""
    total = max(len(profile["watched"]), 1)
    count: dict[int, int] = {}
    for tid in profile["watched"]:
        year = _year((profile["details"].get(tid) or {}).get("year"))
        if year:
            count[year // 10 * 10] = count.get(year // 10 * 10, 0) + 1
    decades = range(1950, today_taipei().year // 10 * 10 + 1, 10)
    base = 1 / len(decades)
    scores = {d: (count.get(d, 0) / total) / base for d in decades}
    blind = {d: s for d, s in scores.items() if s < CONFIG["blindSpotThreshold"]}
    return blind or dict(sorted(scores.items(), key=lambda kv: kv[1])[:2])


# ── Candidate pools ──────────────────────────────────────────────────────────────────────────


def _candidate(tid: int, d: dict, **extra: Any) -> dict:
    return {
        "tmdbId": tid,
        "title": d.get("title"),
        "year": _year(d.get("year")),
        "genreIds": [g for g in d.get("genreIds") or [] if is_integer(g)],
        "genres": d.get("genres") or [],
        "voteAverage": d.get("voteAverage"),
        "voteCount": d.get("voteCount"),
        "collectionId": d.get("collectionId"),
        "directorId": ((d.get("directors") or [{}])[0] or {}).get("id"),
        "director": ((d.get("directors") or [{}])[0] or {}).get("name"),
        "poster": d.get("poster"),
        **extra,
    }


def _genre_vector(genre_ids: list[int]) -> dict[int, float]:
    return dict.fromkeys(genre_ids, 1.0)


async def _comfort_pool(profile: dict, token: str, excluded: set[int]) -> list[dict]:
    sources: dict[int, dict] = {}

    def add(tid: int | None, source: str, seed: int | None = None) -> None:
        if tid and tid not in excluded and tid not in sources:
            sources[tid] = {"source": source, "seed": seed}

    status, body = await _trakt("GET", "/recommendations/movies", token, {"limit": 100, "ignore_collected": "true", "ignore_watchlisted": "false", "extended": "full"})
    if status == 200 and isinstance(body, list):
        for movie in body:
            add(_tmdb_id(movie), "trakt_rec")
    rng = random.Random()
    liked = [tid for tid, w in profile["weights"].items() if w >= CONFIG["likedThreshold"] and profile["traktIds"].get(tid)]
    for seed in rng.sample(liked, min(CONFIG["relatedSeeds"], len(liked))):
        status, body = await _trakt("GET", f"/movies/{profile['traktIds'][seed]}/related", token, {"limit": 20})
        if status == 200 and isinstance(body, list):
            for movie in body:
                add(_tmdb_id(movie), "related", seed)
    for person_id, _ in sorted(profile["directorPref"].items(), key=lambda kv: -kv[1])[: CONFIG["directorsForRecall"]]:
        credits = await person_credits(person_id)
        for movie in sorted(credits["crew"], key=lambda m: -(m.get("vote_count") or 0))[:20]:
            add(movie["id"], "director", person_id)
    details = await load_details(list(sources))
    pool = []
    watchlist = set(profile["watchlist"])
    for tid, info in sources.items():
        d = details.get(tid)
        if not d or not d.get("releaseDate") or d["releaseDate"] > today_taipei().isoformat():
            continue
        if d.get("collectionId") in profile["dislikedCollections"]:
            continue
        people_hit = max([profile["directorPref"].get(p.get("id"), 0.0) for p in d.get("directors") or []] + [profile["actorPref"].get(p.get("id"), 0.0) for p in (d.get("cast") or [])[:5]] + [0.0])
        score = (
            0.40 * _cosine(_genre_vector(d.get("genreIds") or []), profile["genrePref"])
            + 0.25 * _clamp(people_hit)
            + 0.20 * bayes(d.get("voteAverage"), d.get("voteCount"), CONFIG["comfortM"])
            + 0.15 * (1.0 if tid in watchlist else 0.0)
        )
        pool.append(_candidate(tid, d, score=round(score, 4), inWatchlist=tid in watchlist, **info))
    return _diversify(pool, seed_key="seed")


async def _explore_pool(profile: dict, excluded: set[int], blind_genre_scores: dict[int, float], blind_decade_scores: dict[int, float]) -> list[dict]:
    found: dict[int, dict] = {}
    for gid, blind in blind_genre_scores.items():
        for movie in await genre_classics(gid):
            if movie["id"] not in excluded and movie["id"] not in found:
                found[movie["id"]] = {"source": "genre", "blindGenre": gid, "blind": 1 - min(blind, 1.0)}
    for decade, blind in blind_decade_scores.items():
        for movie in await decade_classics(decade):
            if movie["id"] not in excluded and movie["id"] not in found:
                found[movie["id"]] = {"source": "decade", "blindDecade": decade, "blind": 1 - min(blind, 1.0)}
    details = await load_details(list(found))
    # Series: a candidate that is the first film of a series the person has not started.
    collections: dict[int, list[dict]] = {}
    for tid in found:
        cid = (details.get(tid) or {}).get("collectionId")
        if is_integer(cid) and cid not in collections and cid not in profile["dislikedCollections"]:
            collections[cid] = await collection_parts(cid)
    watched = set(profile["watched"])
    cfg = CONFIG["collection"]
    pool = []
    for tid, info in found.items():
        d = details.get(tid)
        if not d:
            continue
        franchise = 0.0
        series_name = None
        parts = collections.get(d.get("collectionId")) or []
        if parts and parts[0]["id"] == tid and not any(p["id"] in watched for p in parts):
            votes = sum(p.get("vote_count") or 0 for p in parts)
            avg = sum((p.get("vote_average") or 0) * (p.get("vote_count") or 0) for p in parts) / votes if votes else 0
            if avg >= cfg["minAvg"] and votes >= cfg["minTotalVotes"] and (parts[0].get("vote_average") or 0) >= cfg["minFirstFilm"]:
                franchise, series_name = 1.0, parts[0].get("name")
        score = 0.40 * bayes(d.get("voteAverage"), d.get("voteCount"), CONFIG["exploreM"]) + 0.25 * _clamp(math.log10(max(d.get("voteCount") or 1, 1)) / 6) + 0.20 * info["blind"] + 0.15 * franchise
        pool.append(_candidate(tid, d, score=round(score, 4), inWatchlist=tid in set(profile["watchlist"]), franchiseStart=bool(franchise), seriesName=series_name, **info))
    return _diversify(pool, seed_key="blindGenre")


async def _bridge_pool(profile: dict, excluded: set[int], blind_genre_scores: dict[int, float]) -> list[dict]:
    found: dict[int, dict] = {}
    people = [(pid, "director", pref) for pid, pref in sorted(profile["directorPref"].items(), key=lambda kv: -kv[1])[: CONFIG["bridgeDirectors"]]]
    people += [(pid, "actor", pref) for pid, pref in sorted(profile["actorPref"].items(), key=lambda kv: -kv[1])[: CONFIG["bridgeActors"]]]
    for pid, role, pref in people:
        credits = await person_credits(pid)
        for movie in credits["crew"] if role == "director" else credits["cast"]:
            hit = [gid for gid in movie.get("genre_ids") or [] if gid in blind_genre_scores]
            if hit and movie["id"] not in excluded and (movie.get("vote_count") or 0) >= 200 and movie["id"] not in found:
                found[movie["id"]] = {"source": "bridge", "personId": pid, "personRole": role, "personPref": pref, "blindGenre": hit[0], "blind": 1 - min(blind_genre_scores[hit[0]], 1.0)}
    details = await load_details(list(found))
    pool = []
    for tid, info in found.items():
        d = details.get(tid)
        if not d or not d.get("releaseDate") or d["releaseDate"] > today_taipei().isoformat() or d.get("collectionId") in profile["dislikedCollections"]:
            continue
        score = 0.40 * _clamp(info["personPref"]) + 0.30 * bayes(d.get("voteAverage"), d.get("voteCount"), CONFIG["comfortM"]) + 0.30 * info["blind"]
        pool.append(_candidate(tid, d, score=round(score, 4), inWatchlist=tid in set(profile["watchlist"]), **info))
    return _diversify(pool, seed_key="personId")


def _diversify(pool: list[dict], *, seed_key: str) -> list[dict]:
    """Best first, with no seed, director, series or genre taking over the pool."""
    kept: list[dict] = []
    per_seed: dict[Any, int] = {}
    per_director: dict[Any, int] = {}
    per_collection: dict[Any, int] = {}
    for c in sorted(pool, key=lambda c: -c["score"]):
        seed, director, collection = c.get(seed_key), c.get("directorId"), c.get("collectionId")
        if seed is not None and per_seed.get(seed, 0) >= CONFIG["maxPerSeed"]:
            continue
        if director is not None and per_director.get(director, 0) >= CONFIG["maxPerDirector"]:
            continue
        if collection is not None and per_collection.get(collection, 0) >= CONFIG["maxPerCollection"]:
            continue
        kept.append(c)
        if seed is not None:
            per_seed[seed] = per_seed.get(seed, 0) + 1
        if director is not None:
            per_director[director] = per_director.get(director, 0) + 1
        if collection is not None:
            per_collection[collection] = per_collection.get(collection, 0) + 1
        if len(kept) >= CONFIG["poolSize"]:
            break
    return kept


async def build_pools(token: str, recent_pick_ids: set[int]) -> dict:
    """Everything a pick needs, built from Trakt and TMDB; cached per profile."""
    profile = await build_profile(token)
    excluded = set(profile["watched"]) | set(profile["ratings"]) | set(profile["hidden"]) | set(profile["disliked"]) | recent_pick_ids
    share = await global_genre_share()
    blind_genre_scores = blind_genres(profile, share)
    blind_decade_scores = blind_decades(profile)
    comfort = await _comfort_pool(profile, token, excluded)
    explore = await _explore_pool(profile, excluded, blind_genre_scores, blind_decade_scores)
    bridge = await _bridge_pool(profile, excluded, blind_genre_scores)
    return {
        "version": POOL_VERSION,
        "builtAt": _now().isoformat(),
        "activities": await last_activities(token),
        "mean": profile["mean"],
        "watchedCount": len(profile["watched"]),
        "ratings": {str(k): v for k, v in profile["ratings"].items()},
        "titles": {str(k): v for k, v in profile["titles"].items()},
        "people": {str(k): {"name": v.get("name"), "originalName": v.get("originalName")} for k, v in profile["people"].items()},
        "genreNames": {str(k): v for k, v in profile["genreNames"].items()},
        "watchlistedAt": {str(k): v for k, v in profile["watchlistedAt"].items()},
        "blindGenres": {str(k): v for k, v in blind_genre_scores.items()},
        "blindDecades": {str(k): v for k, v in blind_decade_scores.items()},
        "pools": {"comfort": comfort, "explore": explore, "bridge": bridge},
    }


# ── Picks ────────────────────────────────────────────────────────────────────────────────────


async def _token_for(row: MovieSubscriberModel) -> str:
    """The owner's access token, refreshed when about to expire. TraktRequired without tokens."""
    tokens = service.tokens_of(row)
    if tokens is None or row.trakt_reauth:
        raise TraktRequired()
    if not trakt.expiring(tokens):
        return tokens.access_token
    async with async_db_session() as db, db.begin():
        locked = await service.by_id(db, row.profile_id, lock=True)
        try:
            fresh = await trakt.refresh(db, tokens.refresh_token, row.trakt_redirect_uri or "")
        except trakt.TraktAuthError as error:
            if locked:
                locked.trakt_reauth = True
            raise TraktRequired() from error
        if locked:
            service.store_tokens(locked, fresh, row.trakt_redirect_uri)
    return fresh.access_token


async def _recent_picks(db: AsyncSession, profile_id: str) -> list[MoviePickModel]:
    since = _now().replace(tzinfo=None) - timedelta(days=NO_REPEAT_DAYS)
    rows = await db.scalars(select(MoviePickModel).where(MoviePickModel.profile_id == profile_id, MoviePickModel.shown_at >= since))
    return list(rows)


async def _pools_for(row: MovieSubscriberModel, token: str, recent_ids: set[int]) -> dict:
    cached = (await read_cache(POOL_NAMESPACE, [row.profile_id])).get(row.profile_id)
    if cached and cached.get("version") == POOL_VERSION and str(cached.get("builtAt", "")) >= (_now() - POOL_MAX_AGE).isoformat():
        try:
            if await last_activities(token) == cached.get("activities"):
                return cached
        except SourceUnavailable:
            return cached
    pools = await build_pools(token, recent_ids)
    await write_cache(POOL_NAMESPACE, {row.profile_id: pools})
    return pools


def _mode_weights(picks: list[MoviePickModel]) -> dict[str, float]:
    """The configured mix, nudged toward the modes whose picks got 想看 rather than 换一部."""
    wants = dict.fromkeys(MODES, 0)
    skips = dict.fromkeys(MODES, 0)
    for pick in picks:
        if pick.mode in wants:
            if pick.action == "watchlist":
                wants[pick.mode] += 1
            elif pick.action == "skip":
                skips[pick.mode] += 1
    weights = {m: CONFIG["initialWeights"][m] * (wants[m] + 1) / (wants[m] + skips[m] + 2) for m in MODES}
    total = sum(weights.values())
    return {m: w / total for m, w in weights.items()}


def _matches(c: dict, filters: dict) -> bool:
    if filters.get("genre") is not None and filters["genre"] not in c.get("genreIds", []):
        return False
    decade = filters.get("decade")
    if decade is not None and not (c.get("year") and decade <= c["year"] <= decade + 9):
        return False
    return True


def _reason(c: dict, mode: str, pools: dict, profile_mean: float) -> str:
    titles, people, genres = pools["titles"], pools["people"], pools["genreNames"]
    seed = str(c.get("seed")) if c.get("seed") is not None else None
    rating = pools["ratings"].get(seed) if seed else None
    rated = f"你给《{titles.get(seed, '?')}》打了 {rating:g} 分" if seed and rating is not None else (f"你看过《{titles.get(seed, '?')}》" if seed else None)
    if c.get("inWatchlist") and c.get("source") != "trakt_rec":
        listed = pools["watchlistedAt"].get(str(c["tmdbId"]))
        months = None
        if listed:
            try:
                months = max(1, round((_now() - datetime.fromisoformat(listed.replace("Z", "+00:00"))).days / 30))
            except ValueError:
                months = None
        return f"这部在你的想看列表里躺了 {months} 个月" if months else "这部在你的想看列表里"
    if mode == "comfort":
        if c.get("source") == "related" and rated:
            return f"{rated}，这部和它很像"
        if c.get("source") == "director":
            name = (people.get(str(c.get("seed"))) or {}).get("name") or c.get("director") or "同一位导演"
            return f"你喜欢导演 {name}，这是他的另一部作品"
        return "Trakt 根据你的观看记录推荐"
    if mode == "bridge":
        person = (people.get(str(c.get("personId"))) or {}).get("name") or "这位影人"
        genre = genres.get(str(c.get("blindGenre"))) or c.get("genres", ["这个类型"])[0]
        return f"你喜欢 {person}，但几乎没看过{genre}片，这部两样都有"
    if c.get("franchiseStart") and c.get("seriesName"):
        return f"这是《{c['seriesName']}》系列的第一部，适合入坑"
    if c.get("source") == "decade":
        return f"你几乎没看过 {c.get('blindDecade')} 年代的电影，这是那十年里票数最高的几部之一（{c.get('voteAverage')} 分，{c.get('voteCount'):,} 票）"
    genre = genres.get(str(c.get("blindGenre"))) or (c.get("genres") or ["这个类型"])[0]
    return f"你几乎没看过{genre}片，这是该类型票数最高的几部之一（{c.get('voteAverage')} 分，{c.get('voteCount'):,} 票）"


async def _card(c: dict) -> dict:
    """What the bot shows: the candidate plus the overview, runtime and cast from TMDB."""
    status, body = await tmdb_get(f"/movie/{c['tmdbId']}", {"language": language(), "append_to_response": "credits,external_ids"})
    body = body if status == 200 and isinstance(body, dict) else {}
    cast = [p.get("name") for p in ((body.get("credits") or {}).get("cast") or [])[:3] if p.get("name")]
    poster = body.get("poster_path")
    return {
        "tmdbId": c["tmdbId"],
        "title": body.get("title") or c.get("title"),
        "originalTitle": body.get("original_title"),
        "year": c.get("year") or _year(body.get("release_date")),
        "overview": (body.get("overview") or "")[:600] or None,
        "runtime": body.get("runtime") if is_integer(body.get("runtime")) and body["runtime"] > 0 else None,
        "genres": [g.get("name") for g in body.get("genres") or []] or c.get("genres") or [],
        "director": c.get("director"),
        "cast": cast,
        "poster": f"{POSTER_BASE}{poster}" if poster else c.get("poster"),
        "voteAverage": body.get("vote_average") if is_number(body.get("vote_average")) else c.get("voteAverage"),
        "voteCount": body.get("vote_count") if is_integer(body.get("vote_count")) else c.get("voteCount"),
        "imdbId": (body.get("external_ids") or {}).get("imdb_id") or body.get("imdb_id"),
        "inWatchlist": bool(c.get("inWatchlist")),
    }


async def pick(db: AsyncSession, row: MovieSubscriberModel, filters: dict, skip_pick_id: str | None = None) -> dict:
    """One recommendation for the profile, recorded as a pick. filters: genre (TMDB genre id),
    decade (e.g. 1990), maxRuntime (minutes). skip_pick_id: the pick this one replaces (换一部)."""
    token = await _token_for(row)
    today = today_taipei().isoformat()
    recent = await _recent_picks(db, row.profile_id)
    if skip_pick_id:
        for previous in recent:
            if previous.pick_id == skip_pick_id and previous.action is None:
                previous.action = "skip"
    used = sum(1 for p in recent if p.day == today)
    if used >= DAILY_PICKS:
        raise DailyLimit(next_midnight_taipei())
    recent_ids = {p.tmdb_id for p in recent}
    pools = await _pools_for(row, token, recent_ids)
    rng = random.Random()
    cold = pools["watchedCount"] < CONFIG["coldStartMinWatched"]
    weights = _mode_weights(recent)
    order = ["explore"] if cold else rng.choices(MODES, weights=[weights[m] for m in MODES], k=1)
    # Fallbacks: a thin bridge pool hands over to comfort; an empty pool tries the others.
    for mode in order + [m for m in MODES if m not in order]:
        candidates = [c for c in pools["pools"][mode] if c["tmdbId"] not in recent_ids and _matches(c, filters)]
        if mode == "bridge" and len(candidates) < CONFIG["bridgeMinPoolSize"]:
            continue
        tried: set[int] = set()
        while candidates and len(tried) < 8:
            chosen = _softmax_pick(candidates, CONFIG["temperature"], rng)
            tried.add(chosen["tmdbId"])
            card = await _card(chosen)
            if filters.get("maxRuntime") and card["runtime"] and card["runtime"] > filters["maxRuntime"]:
                candidates = [c for c in candidates if c["tmdbId"] != chosen["tmdbId"]]
                continue
            reason = _reason(chosen, mode, pools, pools["mean"])
            pick_row = MoviePickModel(
                profile_id=row.profile_id, tmdb_id=chosen["tmdbId"], mode=mode, day=today, shown_at=_now().replace(tzinfo=None), title=card["title"] or str(chosen["tmdbId"]), reason=reason, data=card
            )
            db.add(pick_row)
            await db.flush()
            return {**card, "pickId": pick_row.pick_id, "mode": mode, "reason": reason, "cold": cold, "remainingToday": DAILY_PICKS - used - 1}
    raise NothingToPick()


async def _pick_row(db: AsyncSession, row: MovieSubscriberModel, pick_id: str) -> MoviePickModel:
    found = await db.get(MoviePickModel, pick_id)
    if found is None or found.profile_id != row.profile_id:
        raise LookupError(pick_id)
    return found


async def _drop_from_pool(profile_id: str, tmdb_id: int) -> None:
    cached = (await read_cache(POOL_NAMESPACE, [profile_id])).get(profile_id)
    if not cached:
        return
    for mode in MODES:
        cached["pools"][mode] = [c for c in cached["pools"].get(mode, []) if c["tmdbId"] != tmdb_id]
    await write_cache(POOL_NAMESPACE, {profile_id: cached})


async def want(db: AsyncSession, row: MovieSubscriberModel, pick_id: str) -> dict:
    """想看: the film goes on the person's Trakt watchlist."""
    token = await _token_for(row)
    found = await _pick_row(db, row, pick_id)
    status, body = await _trakt("POST", "/sync/watchlist", token, json={"movies": [{"ids": {"tmdb": found.tmdb_id}}]})
    if status not in (200, 201):
        raise SourceUnavailable(f"Trakt 暂时无法加入想看列表（HTTP {status}）")
    found.action = "watchlist"
    found.acted_at = _now().replace(tzinfo=None)
    await db.flush()
    await _drop_from_pool(row.profile_id, found.tmdb_id)
    return {"pickId": pick_id, "action": "watchlist", "added": ((body or {}).get("added") or {}).get("movies", 0) if isinstance(body, dict) else None}


async def watched(db: AsyncSession, row: MovieSubscriberModel, pick_id: str, rating: int | None) -> dict:
    """看过了: with the person's rating (given now, or already on Trakt) the film is marked watched
    and rated on Trakt. Without one, nothing is written and `rated` is False so the bot can ask."""
    token = await _token_for(row)
    found = await _pick_row(db, row, pick_id)
    existing = None
    if rating is None:
        status, body = await _trakt("GET", "/sync/ratings/movies", token, {"limit": 100, "page": 1})
        # The rating, if the film was rated recently; older ones come from the cached profile.
        pools = (await read_cache(POOL_NAMESPACE, [row.profile_id])).get(row.profile_id) or {}
        existing = (pools.get("ratings") or {}).get(str(found.tmdb_id))
        if status == 200 and isinstance(body, list):
            for entry in body:
                if _tmdb_id(entry.get("movie")) == found.tmdb_id and is_number(entry.get("rating")):
                    existing = entry["rating"]
        if existing is None:
            return {"pickId": pick_id, "action": None, "rated": False}
    value = rating if rating is not None else int(existing)
    now = _now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    movie = {"ids": {"tmdb": found.tmdb_id}}
    if rating is not None:
        status, _ = await _trakt("POST", "/sync/ratings", token, json={"movies": [{**movie, "rating": value, "rated_at": now}]})
        if status not in (200, 201):
            raise SourceUnavailable(f"Trakt 暂时无法写入评分（HTTP {status}）")
    status, _ = await _trakt("POST", "/sync/history", token, json={"movies": [{**movie, "watched_at": now}]})
    if status not in (200, 201):
        raise SourceUnavailable(f"Trakt 暂时无法标记看过（HTTP {status}）")
    found.action = "watched"
    found.rating = value
    found.acted_at = _now().replace(tzinfo=None)
    await db.flush()
    await _drop_from_pool(row.profile_id, found.tmdb_id)
    # The statistics follow the new history on the next sync (no rate limit: this is one film).
    async with async_db_session() as session, session.begin():
        locked = await service.by_id(session, row.profile_id, lock=True)
        if locked:
            locked.sync_requested = True
    return {"pickId": pick_id, "action": "watched", "rated": True, "rating": value}


async def picks_today(db: AsyncSession, profile_id: str) -> int:
    return int(await db.scalar(select(func.count()).select_from(MoviePickModel).where(MoviePickModel.profile_id == profile_id, MoviePickModel.day == today_taipei().isoformat())) or 0)


def pick_out(row: MoviePickModel) -> dict:
    return {
        "pickId": row.pick_id,
        "tmdbId": row.tmdb_id,
        "mode": row.mode,
        "day": row.day,
        "title": row.title,
        "reason": row.reason,
        "action": row.action,
        "rating": row.rating,
        "shownAt": row.shown_at.isoformat() if row.shown_at else None,
    }


__all__ = ["DailyLimit", "NothingToPick", "TraktRequired", "pick", "want", "watched", "picks_today", "pick_out", "logger"]
