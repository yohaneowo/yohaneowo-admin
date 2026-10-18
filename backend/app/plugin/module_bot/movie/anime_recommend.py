"""Random anime picks for the bot's `/anime recommend` (spec: anime-recommend-spec in the site's docs),
the anime counterpart of recommend.py. A pick is one AniList entry (a TV season, a film, an ONA).

Five pools, each scored and sampled with a temperature like the movie picks:

- sequel: the next season of a series the person finished and liked;
- comfort: AniList's community recommendations for their best-rated anime, and more from their
  favourite studios and directors;
- bridge: a genre they hardly watch, by a studio or director they love;
- explore: the best-rated anime of genres and decades they hardly watch;
- airing: this season's airing anime close to their taste (off when the website switch says so,
  unless the command asks for airing only).

The taste comes from the statistics snapshot (AniList genres, tags and staff were looked up during
the sync), so only the watchlist, the hidden list and "did the lists change" are read from Trakt.
Pools are cached for a day, or until Trakt's last_activities or the snapshot changes. 想看 / 看过了
write to Trakt through the community AniList → TMDB mapping (anime_ids.py); a season whose numbering
can't be trusted gets links only.
"""

import asyncio
import json
import math
import random
import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_db_session
from app.core.logger import logger
from app.plugin.module_ai_usage.gateway import service as ai_gateway
from app.plugin.module_ai_usage.gateway.schema import AiCallerSchema, AiGatewayRequestSchema

from . import anime_ids, bonds, credits, service
from .model import AnimePickModel, MovieSubscriberModel
from .recommend import DailyLimit, NothingToPick, TraktRequired, _token_for, _trakt, _trakt_all, next_midnight_taipei, today_taipei
from .sources import anilist, bangumi
from .sources.base import SourceUnavailable
from .sync.common import is_integer, is_number, read_cache, trakt_get, write_cache

POOL_NAMESPACE = "anime_recommend_pool"
GLOBAL_NAMESPACE = "anime_recommend_global"
PROGRESS_NAMESPACE = "anime_recommend_progress"
TRAKT_SHOW_NAMESPACE = "anime_trakt_show"
POOL_VERSION = 1
POOL_MAX_AGE = timedelta(hours=24)
AIRING_MAX_AGE = timedelta(hours=6)
TRAKT_SHOW_MAX_AGE = timedelta(days=7)

DAILY_PICKS = 3
REFUNDS_A_DAY = 3
NO_REPEAT_DAYS = 30

MODES = ("sequel", "comfort", "bridge", "explore", "airing")
CONFIG: dict[str, Any] = {
    "defaultMeanRating": 7.0,
    "unratedWeight": 0.5,
    "dislikedThreshold": -1.5,
    "temperature": 0.15,
    # Popularity (AniList's "on someone's list" count) plays the part of a vote count.
    "bayesM": 20000,
    "globalMean": 7.0,
    "minPopularity": 15000,
    "initialWeights": {"sequel": 0.3, "comfort": 0.3, "bridge": 0.1, "explore": 0.15, "airing": 0.15},
    "sequelSeeds": 50,
    "recommendationSeeds": 20,
    "recommendationsPerSeed": 8,
    "studios": 3,
    "directors": 3,
    "blindGenres": 3,
    "blindRatio": 0.35,
    "poolSize": 60,
    "maxPerStudio": 3,
    "bridgeMinPoolSize": 3,
    "watchlistBonus": 0.15,
}
# Share of popular anime carrying each AniList genre (the site's genre-weights.ts, sampled
# 2026-10-18); a genre the person watches far less than this is a blind spot.
GENRE_BASE = {
    "Action": 0.4872,
    "Comedy": 0.4656,
    "Drama": 0.4067,
    "Fantasy": 0.3684,
    "Romance": 0.3546,
    "Adventure": 0.2829,
    "Supernatural": 0.2711,
    "Slice of Life": 0.2603,
    "Sci-Fi": 0.164,
    "Mystery": 0.1611,
    "Psychological": 0.1493,
    "Ecchi": 0.1228,
    "Thriller": 0.0658,
    "Horror": 0.053,
    "Sports": 0.0363,
    "Mecha": 0.0305,
    "Music": 0.0236,
    "Mahou Shoujo": 0.0059,
}
FORMATS = {"TV", "TV_SHORT", "ONA", "MOVIE"}
DIRECTOR_ROLES = credits.ANILIST_ROLES["director"]

CANDIDATE = """id idMal type format status episodes season seasonYear isAdult averageScore meanScore popularity
  startDate { year } genres tags { name rank isMediaSpoiler } studios(isMain: true) { nodes { id name } }
  title { romaji english native } coverImage { extraLarge large color }
  relations { edges { relationType node { id type } } }"""
SEQUEL_QUERY = f"""query ($ids: [Int]) {{ Page(perPage: 50) {{ media(id_in: $ids) {{ id
  relations {{ edges {{ relationType node {{ {CANDIDATE} }} }} }} }} }} }}"""
RECOMMENDATION_QUERY = f"""query ($ids: [Int], $per: Int) {{ Page(perPage: 10) {{ media(id_in: $ids) {{ id
  recommendations(sort: [RATING_DESC], perPage: $per) {{ nodes {{ rating mediaRecommendation {{ {CANDIDATE} }} }} }} }} }} }}"""
STUDIO_QUERY = f"""query ($id: Int) {{ Studio(id: $id) {{ name
  media(isMain: true, sort: [POPULARITY_DESC], perPage: 25) {{ nodes {{ {CANDIDATE} }} }} }} }}"""
DIRECTOR_QUERY = f"""query ($id: Int) {{ Staff(id: $id) {{ name {{ native full }}
  staffMedia(type: ANIME, sort: [POPULARITY_DESC], perPage: 25) {{ edges {{ staffRole node {{ {CANDIDATE} }} }} }} }} }}"""
GENRE_QUERY = f"""query ($genre: String, $pop: Int) {{ Page(perPage: 25) {{ media(type: ANIME, genre_in: [$genre],
  sort: [SCORE_DESC], popularity_greater: $pop, isAdult: false, format_in: [TV, MOVIE, ONA]) {{ {CANDIDATE} }} }} }}"""
DECADE_QUERY = f"""query ($from: FuzzyDateInt, $to: FuzzyDateInt, $pop: Int) {{ Page(perPage: 25) {{ media(type: ANIME,
  startDate_greater: $from, startDate_lesser: $to, sort: [SCORE_DESC], popularity_greater: $pop, isAdult: false,
  format_in: [TV, MOVIE, ONA]) {{ {CANDIDATE} }} }} }}"""
AIRING_QUERY = f"""query ($season: MediaSeason, $year: Int, $page: Int) {{ Page(page: $page, perPage: 50) {{
  media(type: ANIME, season: $season, seasonYear: $year, status: RELEASING, sort: [POPULARITY_DESC], isAdult: false) {{
  {CANDIDATE} }} }} }}"""

AI_MODEL = "claude-sonnet-5-5"
AI_CANDIDATES = 12
AI_TIMEOUT_SECONDS = 25
AI_SYSTEM = (
    "你是一位懂动漫、也了解这位用户口味的朋友，正在替用户从几部候选里挑一部接下来追的动漫。"
    '只能从候选清单里选，回答一律是一个 JSON 对象：{"anilistId": 数字, "reason": "字串"}，不要其他文字。'
    "reason 用简体中文、口语、最多 60 字、两句以内，要点出这部和用户口味的具体关联（喜欢的类型、制作公司、监督、相似的作品），不剧透，不要重复作品名。"
)
MODE_INTENT = {
    "sequel": "这一轮的目标是「接着追」：候选都是用户看完而且喜欢的系列的下一季，选最值得马上接着看的一部。",
    "comfort": "这一轮的目标是「猜你喜欢」：选最可能合用户胃口的一部。",
    "bridge": "这一轮的目标是「跨出一步」：候选都是用户很少看的类型，但出自用户喜欢的制作公司或监督，选最容易入门的一部。",
    "explore": "这一轮的目标是「开新坑」：候选都是用户盲区里评价最高的作品，选最适合入门、口碑最稳的一部。",
    "airing": "这一轮的目标是「本季新番」：候选都是这一季正在播的作品，选最合用户口味、最值得现在开始追的一部。",
}
MODE_LABEL = {"sequel": "续作", "comfort": "猜你喜欢", "bridge": "跨界", "explore": "新坑", "airing": "新番"}


def _now() -> datetime:
    return datetime.now(UTC)


def quota_for(row: MovieSubscriberModel) -> int:
    return row.anime_pick_quota if is_integer(row.anime_pick_quota) and row.anime_pick_quota > 0 else DAILY_PICKS


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def bayes(score: float | None, popularity: int | None) -> float:
    """A 0..1 quality from AniList's score (0-10) and popularity: little-known entries are pulled
    toward the global mean."""
    if not is_number(score) or not is_integer(popularity) or popularity <= 0:
        return 0.0
    m, c = CONFIG["bayesM"], CONFIG["globalMean"]
    rating = (popularity / (popularity + m)) * score + (m / (popularity + m)) * c
    return _clamp((rating - 5.5) / 3.5)


def _normalize(pref: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(v * v for v in pref.values()))
    return {k: v / norm for k, v in pref.items()} if norm else {}


def _cosine(vector: dict[str, float], pref: dict[str, float]) -> float:
    if not vector or not pref:
        return 0.0
    dot = sum(vector.get(k, 0.0) * v for k, v in pref.items())
    length = math.sqrt(sum(v * v for v in vector.values()))
    return _clamp(dot / length) if length else 0.0


def _softmax_pick(candidates: list[dict], rng: random.Random) -> dict:
    scores = [c["score"] / CONFIG["temperature"] for c in candidates]
    top = max(scores)
    return rng.choices(candidates, weights=[math.exp(s - top) for s in scores], k=1)[0]


def current_season(today: date | None = None) -> tuple[str, int]:
    today = today or datetime.now(UTC).date()
    return ("WINTER", "SPRING", "SUMMER", "FALL")[(today.month - 1) // 3], today.year


async def _report(profile_id: str, stage: str | None) -> None:
    try:
        await write_cache(PROGRESS_NAMESPACE, {profile_id: {"stage": stage, "at": _now().isoformat()}})
    except Exception as error:  # noqa: BLE001 - progress is cosmetic
        logger.debug(f"[anime-recommend] progress write failed: {error}")


async def progress_of(profile_id: str) -> dict:
    found = (await read_cache(PROGRESS_NAMESPACE, [profile_id])).get(profile_id) or {}
    return {"stage": found.get("stage"), "at": found.get("at")}


async def _query(text: str, variables: dict) -> dict:
    async with async_db_session() as db, db.begin():
        return await anilist.query(db, text, variables) or {}


# ── Taste (from the statistics snapshot) ─────────────────────────────────────────────────────


def _rating_of(item: dict) -> float | None:
    if is_number(item.get("myRating")):
        return float(item["myRating"])
    rated = [s["myRating"] for s in item.get("seasons") or [] if is_number(s.get("myRating"))]
    return max(rated) if rated else None


def _vector(genres: list[str], tags: list[str]) -> dict[str, float]:
    return {**{f"g:{g}": 1.0 for g in genres}, **{f"t:{t}": 0.6 for t in tags}}


def build_profile(snapshot: dict | None) -> dict:
    """What the person watched and likes, from the snapshot's anime section."""
    section = (snapshot or {}).get("anime") or {}
    items = section.get("items") or []
    mean = section.get("mean") if is_number(section.get("mean")) else CONFIG["defaultMeanRating"]
    watched: set[int] = set()
    disliked: set[int] = set()
    sequel_seeds: list[dict] = []
    liked: list[dict] = []
    taste: dict[str, float] = {}
    studios: dict[int, dict] = {}
    directors: dict[int, dict] = {}
    genre_count: dict[str, int] = {}
    decades: dict[int, int] = {}
    for item in items:
        watched_seasons = bonds.watched_seasons(item)
        seasons = sorted((s for s in item.get("seasons") or [] if is_integer(s.get("number"))), key=lambda s: s["number"])
        seen = [s for s in seasons if (item.get("id"), s["number"]) in watched_seasons and is_integer(s.get("anilistId"))]
        watched.update(s["anilistId"] for s in seen)
        rating = _rating_of(item)
        weight = rating - mean if rating is not None else CONFIG["unratedWeight"]
        genres = [g for g in item.get("genres") or [] if isinstance(g, str)]
        tags = [t for t in item.get("tags") or [] if isinstance(t, str)]
        for key, value in _vector(genres, tags).items():
            taste[key] = taste.get(key, 0.0) + weight * value
        for genre in genres:
            genre_count[genre] = genre_count.get(genre, 0) + 1
        year = item.get("year")
        if is_integer(year):
            decades[year // 10 * 10] = decades.get(year // 10 * 10, 0) + 1
        for person in item.get("staff") or []:
            target = studios if person.get("role") == "studio" else directors if person.get("role") == "director" else None
            if target is None or not is_integer(person.get("id")) or person["id"] <= 0:
                continue
            entry = target.setdefault(person["id"], {"id": person["id"], "name": person.get("name"), "weight": 0.0, "count": 0})
            entry["weight"] += weight
            entry["count"] += 1
        if not seen:
            continue
        if rating is not None and rating - mean <= CONFIG["dislikedThreshold"]:
            disliked.update(s["anilistId"] for s in seasons if is_integer(s.get("anilistId")))
        if rating is not None and rating >= mean:
            sequel_seeds.append({"anilistId": seen[-1]["anilistId"], "rating": rating, "title": item.get("title")})
            liked.append({"anilistId": seen[0]["anilistId"], "rating": rating, "title": item.get("title")})
    total = max(1, len(items))
    blind = sorted(
        (g for g, base in GENRE_BASE.items() if base >= 0.02 and genre_count.get(g, 0) / total < CONFIG["blindRatio"] * base),
        # The widest gap first; among genres never watched, the common ones (an easier way in).
        key=lambda g: (genre_count.get(g, 0) / total / GENRE_BASE[g], -GENRE_BASE[g]),
    )
    favourite = lambda people: sorted((p for p in people.values() if p["count"] >= 2 and p["weight"] > 0), key=lambda p: -p["weight"])  # noqa: E731
    liked.sort(key=lambda x: -x["rating"])
    sequel_seeds.sort(key=lambda x: -x["rating"])
    return {
        "mean": mean,
        "watchedCount": len(items),
        "watched": watched,
        "disliked": disliked,
        "taste": _normalize(taste),
        "sequelSeeds": sequel_seeds[: CONFIG["sequelSeeds"]],
        "liked": liked,
        "studios": favourite(studios)[: CONFIG["studios"]],
        "directors": favourite(directors)[: CONFIG["directors"]],
        "blindGenres": blind[: CONFIG["blindGenres"]],
        "decades": decades,
        "topGenres": [g for g, _ in sorted(genre_count.items(), key=lambda x: -x[1])[:5]],
    }


# ── Candidates ───────────────────────────────────────────────────────────────────────────────


def candidate(media: dict | None, **extra: Any) -> dict | None:
    """A recommendable AniList entry, or None (not anime, a special or MV, adult, not out yet)."""
    if not media or media.get("type") != "ANIME" or media.get("format") not in FORMATS or media.get("isAdult") or media.get("status") == "NOT_YET_RELEASED":
        return None
    if not is_integer(media.get("id")):
        return None
    title = media.get("title") or {}
    score = media.get("averageScore") or media.get("meanScore")
    tags = [t["name"] for t in media.get("tags") or [] if isinstance(t, dict) and (t.get("rank") or 0) >= 60 and not t.get("isMediaSpoiler")][:6]
    relations = ((media.get("relations") or {}).get("edges")) or []
    prequels = [((e.get("node") or {}).get("id")) for e in relations if e.get("relationType") == "PREQUEL" and (e.get("node") or {}).get("type") == "ANIME"]
    cover = media.get("coverImage") or {}
    return {
        "anilistId": media["id"],
        "malId": media.get("idMal") if is_integer(media.get("idMal")) else None,
        "title": title.get("native") or title.get("romaji"),
        "titleEn": title.get("english") or title.get("romaji"),
        "format": media.get("format"),
        "status": media.get("status"),
        "episodes": media.get("episodes") if is_integer(media.get("episodes")) else None,
        "year": ((media.get("startDate") or {}).get("year")) or media.get("seasonYear"),
        "season": media.get("season"),
        "score": round(score / 10, 1) if is_number(score) else None,
        "popularity": media.get("popularity") if is_integer(media.get("popularity")) else 0,
        "genres": [g for g in media.get("genres") or [] if isinstance(g, str)],
        "tags": tags,
        "studios": [{"id": s["id"], "name": s.get("name")} for s in ((media.get("studios") or {}).get("nodes") or []) if is_integer(s.get("id"))],
        "cover": cover.get("extraLarge") or cover.get("large"),
        "color": cover.get("color"),
        "prequels": [p for p in prequels if is_integer(p)],
        **extra,
    }


def _scored(pool: list[dict]) -> list[dict]:
    """Best first, one per AniList entry, at most maxPerStudio per studio, capped at poolSize."""
    seen: set[int] = set()
    per_studio: dict[int, int] = {}
    out = []
    for c in sorted(pool, key=lambda c: -c["score"]):
        if c["anilistId"] in seen:
            continue
        studio = (c.get("studios") or [{}])[0].get("id")
        if studio is not None and per_studio.get(studio, 0) >= CONFIG["maxPerStudio"]:
            continue
        seen.add(c["anilistId"])
        if studio is not None:
            per_studio[studio] = per_studio.get(studio, 0) + 1
        out.append(c)
    return out[: CONFIG["poolSize"]]


def _fits(c: dict, profile: dict, *, sequel: bool = False) -> bool:
    """Not watched or disliked; outside the sequel pool, no later season of an unwatched series."""
    if c["anilistId"] in profile["watched"] or c["anilistId"] in profile["disliked"]:
        return False
    return sequel or not c["prequels"] or any(p in profile["watched"] for p in c["prequels"])


async def _sequel_pool(profile: dict) -> list[dict]:
    seeds = {s["anilistId"]: s for s in profile["sequelSeeds"]}
    if not seeds:
        return []
    data = await _query(SEQUEL_QUERY, {"ids": list(seeds)})
    pool = []
    for media in (data.get("Page") or {}).get("media") or []:
        seed = seeds.get(media.get("id"))
        for edge in ((media.get("relations") or {}).get("edges")) or []:
            if edge.get("relationType") != "SEQUEL":
                continue
            c = candidate(edge.get("node"), source="sequel", seedTitle=seed and seed["title"], seedRating=seed and seed["rating"])
            if c and _fits(c, profile, sequel=True):
                liking = _clamp(((seed or {}).get("rating", profile["mean"]) - profile["mean"] + 1) / 3)
                c["score"] = 0.5 * liking + 0.5 * bayes(c["score"], c["popularity"])
                pool.append(c)
    return _scored(pool)


async def _people_works(profile: dict) -> list[dict]:
    """The favourite studios' and directors' best-known works (for comfort and bridge)."""
    works = []
    for studio in profile["studios"]:
        data = await _query(STUDIO_QUERY, {"id": studio["id"]})
        for node in ((data.get("Studio") or {}).get("media") or {}).get("nodes") or []:
            if c := candidate(node, source="studio", person=studio["name"], personWeight=studio["weight"]):
                works.append(c)
    for director in profile["directors"]:
        data = await _query(DIRECTOR_QUERY, {"id": director["id"]})
        for edge in ((data.get("Staff") or {}).get("staffMedia") or {}).get("edges") or []:
            if credits._base_role(edge.get("staffRole")) in DIRECTOR_ROLES and (c := candidate(edge.get("node"), source="director", person=director["name"], personWeight=director["weight"])):  # noqa: SLF001
                works.append(c)
    return works


async def _comfort_pool(profile: dict, people_works: list[dict]) -> list[dict]:
    pool = []
    seeds = profile["liked"][: CONFIG["recommendationSeeds"]]
    titles = {s["anilistId"]: s for s in seeds}
    for start in range(0, len(seeds), 10):
        data = await _query(RECOMMENDATION_QUERY, {"ids": [s["anilistId"] for s in seeds[start : start + 10]], "per": CONFIG["recommendationsPerSeed"]})
        for media in (data.get("Page") or {}).get("media") or []:
            seed = titles.get(media.get("id")) or {}
            for node in ((media.get("recommendations") or {}).get("nodes")) or []:
                c = candidate(node.get("mediaRecommendation"), source="recommendation", seedTitle=seed.get("title"), seedRating=seed.get("rating"))
                if c and _fits(c, profile):
                    votes = _clamp((node.get("rating") or 0) / 100)
                    c["score"] = 0.45 * _cosine(_vector(c["genres"], c["tags"]), profile["taste"]) + 0.35 * bayes(c["score"], c["popularity"]) + 0.2 * votes
                    pool.append(c)
    top_weight = max([p["weight"] for p in profile["studios"] + profile["directors"]] or [1.0])
    for work in people_works:
        c = dict(work)
        if _fits(c, profile) and not set(c["genres"]) & set(profile["blindGenres"]):
            c["score"] = 0.45 * _cosine(_vector(c["genres"], c["tags"]), profile["taste"]) + 0.35 * bayes(c["score"], c["popularity"]) + 0.2 * _clamp(c["personWeight"] / top_weight)
            pool.append(c)
    return _scored(pool)


def _bridge_pool(profile: dict, people_works: list[dict]) -> list[dict]:
    blind = set(profile["blindGenres"])
    top_weight = max([p["weight"] for p in profile["studios"] + profile["directors"]] or [1.0])
    pool = []
    for work in people_works:
        genres = set(work["genres"]) & blind
        if genres and _fits(work, profile):
            c = {**work, "blindGenre": sorted(genres)[0]}
            c["score"] = 0.5 * _clamp(c["personWeight"] / top_weight) + 0.5 * bayes(c["score"], c["popularity"])
            pool.append(c)
    return _scored(pool)


async def _explore_pool(profile: dict) -> list[dict]:
    pool = []
    for rank, genre in enumerate(profile["blindGenres"]):
        data = await _query(GENRE_QUERY, {"genre": genre, "pop": CONFIG["minPopularity"]})
        for media in (data.get("Page") or {}).get("media") or []:
            c = candidate(media, source="genre", blindGenre=genre)
            if c and _fits(c, profile):
                c["score"] = 0.65 * bayes(c["score"], c["popularity"]) + 0.35 * (1 - rank / max(1, len(profile["blindGenres"])))
                pool.append(c)
    # One blind decade: the earliest of the last three the person has barely touched.
    decade = next((d for d in (1990, 2000, 2010) if profile["decades"].get(d, 0) < 2), None)
    if decade:
        data = await _query(DECADE_QUERY, {"from": decade * 10000, "to": (decade + 10) * 10000, "pop": CONFIG["minPopularity"]})
        for media in (data.get("Page") or {}).get("media") or []:
            c = candidate(media, source="decade", blindDecade=decade)
            if c and _fits(c, profile):
                c["score"] = 0.65 * bayes(c["score"], c["popularity"]) + 0.2
                pool.append(c)
    return _scored(pool)


async def airing_now() -> list[dict]:
    """This season's airing anime (shared by everyone, cached AIRING_MAX_AGE)."""
    season, year = current_season()
    key = f"airing:{season}:{year}"
    cached = (await read_cache(GLOBAL_NAMESPACE, [key])).get(key)
    if cached and str(cached.get("fetchedAt", "")) >= (_now() - AIRING_MAX_AGE).isoformat():
        return cached["media"]
    media: list[dict] = []
    for page in (1, 2):
        data = await _query(AIRING_QUERY, {"season": season, "year": year, "page": page})
        media.extend(c for m in (data.get("Page") or {}).get("media") or [] if (c := candidate(m, source="airing")))
    await write_cache(GLOBAL_NAMESPACE, {key: {"media": media, "fetchedAt": _now().isoformat()}})
    return media


def _airing_pool(profile: dict, airing: list[dict]) -> list[dict]:
    pool = []
    top = max([c["popularity"] for c in airing] or [1])
    for work in airing:
        c = dict(work)
        if _fits(c, profile):
            quality = bayes(c["score"], c["popularity"]) if c["score"] else 0.5 * _clamp(c["popularity"] / top)
            c["score"] = 0.6 * _cosine(_vector(c["genres"], c["tags"]), profile["taste"]) + 0.4 * quality
            pool.append(c)
    return _scored(pool)


# ── Trakt lists, targets and pools ───────────────────────────────────────────────────────────


async def last_activities(token: str) -> dict:
    status, body = await _trakt("GET", "/sync/last_activities", token)
    body = body if status == 200 and isinstance(body, dict) else {}
    return {
        "shows": {k: (body.get("shows") or {}).get(k) for k in ("watchlisted_at", "hidden_at", "rated_at")},
        "seasons": {k: (body.get("seasons") or {}).get(k) for k in ("watchlisted_at", "rated_at")},
        "episodes": (body.get("episodes") or {}).get("watched_at"),
        "movies": {k: (body.get("movies") or {}).get(k) for k in ("watched_at", "watchlisted_at", "rated_at")},
    }


async def trakt_lists(token: str) -> dict:
    """TMDB IDs on the watchlist (shows and films) and hidden from recommendations."""
    shows, movies, hidden = await asyncio.gather(
        _trakt_all("/sync/watchlist/shows", token),
        _trakt_all("/sync/watchlist/movies", token),
        _trakt_all("/users/hidden/recommendations", token, {"type": "show"}),
    )

    def ids(entries: list, kind: str) -> list[int]:
        return [tid for e in entries if is_integer(tid := (((e.get(kind) or {}).get("ids")) or {}).get("tmdb"))]

    return {"watchlistShows": ids(shows, "show"), "watchlistMovies": ids(movies, "movie"), "hiddenShows": ids(hidden, "show")}


async def build_pools(row: MovieSubscriberModel, token: str) -> dict:
    await _report(row.profile_id, "lists")
    lists, activities = await asyncio.gather(trakt_lists(token), last_activities(token))
    await _report(row.profile_id, "profile")
    profile = build_profile(row.snapshot)
    await _report(row.profile_id, "pools")
    people_works = await _people_works(profile)
    pools = {
        "sequel": await _sequel_pool(profile),
        "comfort": await _comfort_pool(profile, people_works),
        "bridge": _bridge_pool(profile, people_works),
        "explore": await _explore_pool(profile),
        "airing": _airing_pool(profile, await airing_now()),
    }
    # The watchlist lifts a candidate; the hidden list drops it (both through the TMDB mapping).
    targets = await anime_ids.lookup([c["anilistId"] for pool in pools.values() for c in pool])
    watch_tv, watch_movie, hidden = set(lists["watchlistShows"]), set(lists["watchlistMovies"]), set(lists["hiddenShows"])
    for mode, pool in pools.items():
        kept = []
        for c in pool:
            target = targets.get(c["anilistId"])
            if target and target["type"] == "tv" and target["tmdbId"] in hidden:
                continue
            if target and target["tmdbId"] in (watch_tv if target["type"] == "tv" else watch_movie):
                c["inWatchlist"] = True
                c["score"] = _clamp(c["score"] + CONFIG["watchlistBonus"])
            kept.append(c)
        pools[mode] = kept
    return {
        "version": POOL_VERSION,
        "builtAt": _now().isoformat(),
        "activities": activities,
        "syncedAt": row.synced_at.isoformat() if row.synced_at else None,
        "pools": pools,
        "summary": {
            "watched": profile["watchedCount"],
            "mean": round(profile["mean"], 1),
            "genres": profile["topGenres"],
            "studios": [s["name"] for s in profile["studios"]],
            "directors": [d["name"] for d in profile["directors"]],
            "liked": [{"title": x["title"], "rating": x["rating"]} for x in profile["liked"][:8]],
        },
    }


async def _pools_for(row: MovieSubscriberModel, token: str) -> dict:
    cached = (await read_cache(POOL_NAMESPACE, [row.profile_id])).get(row.profile_id)
    synced = row.synced_at.isoformat() if row.synced_at else None
    if cached and cached.get("version") == POOL_VERSION and cached.get("syncedAt") == synced and str(cached.get("builtAt", "")) >= (_now() - POOL_MAX_AGE).isoformat():
        try:
            if await last_activities(token) == cached.get("activities"):
                return cached
        except SourceUnavailable:
            return cached
    pools = await build_pools(row, token)
    await write_cache(POOL_NAMESPACE, {row.profile_id: pools})
    return pools


async def trakt_show(tmdb_id: int) -> dict | None:
    """Trakt's show for a TMDB show, with its seasons' episode counts (cached TRAKT_SHOW_MAX_AGE)."""
    key = str(tmdb_id)
    cached = (await read_cache(TRAKT_SHOW_NAMESPACE, [key])).get(key)
    if cached and str(cached.get("fetchedAt", "")) >= (_now() - TRAKT_SHOW_MAX_AGE).isoformat():
        return cached.get("show")
    status, _, found = await trakt_get(f"/search/tmdb/{tmdb_id}", {"type": "show"})
    show = (found[0].get("show") if status == 200 and isinstance(found, list) and found else None) or {}
    ids = show.get("ids") or {}
    result = None
    if is_integer(ids.get("trakt")) and ids.get("slug"):
        status, _, seasons = await trakt_get(f"/shows/{ids['slug']}/seasons", {"extended": "full"})
        counts = {str(s["number"]): s.get("episode_count") for s in seasons or [] if status == 200 and is_integer(s.get("number"))}
        result = {"traktId": ids["trakt"], "slug": ids["slug"], "seasons": counts}
    await write_cache(TRAKT_SHOW_NAMESPACE, {key: {"show": result, "fetchedAt": _now().isoformat()}})
    return result


def season_target(mapping: dict, show: dict, episodes: int | None) -> dict | None:
    """Where a TV entry lands on Trakt: the whole season when the counts agree, an episode range
    when the entry is part of a bigger Trakt season (split cours, merged seasons), or None when the
    numbering can't be trusted. watchable False: only the watchlist (episode count unknown)."""
    season = mapping.get("season")
    count = show["seasons"].get(str(season)) if is_integer(season) else None
    if not is_integer(count) or count <= 0:
        return None
    offset = mapping.get("offset") or 0
    base = {"type": "show", "traktId": show["traktId"], "slug": show["slug"], "tmdbId": mapping["tmdbId"], "season": season, "url": f"https://trakt.tv/shows/{show['slug']}/seasons/{season}"}
    if not is_integer(episodes):
        return {**base, "episodes": None, "watchable": False} if offset == 0 else None
    if offset == 0 and episodes == count:
        return {**base, "episodes": None, "watchable": True}
    if offset + episodes <= count:
        return {**base, "episodes": [offset + 1, offset + episodes], "watchable": True}
    return None


async def trakt_target(c: dict) -> dict | None:
    mapping = (await anime_ids.lookup([c["anilistId"]])).get(c["anilistId"])
    if not mapping:
        return None
    try:
        if mapping["type"] == "movie":
            status, _, found = await trakt_get(f"/search/tmdb/{mapping['tmdbId']}", {"type": "movie"})
            movie = (found[0].get("movie") if status == 200 and isinstance(found, list) and found else None) or {}
            ids = movie.get("ids") or {}
            if not is_integer(ids.get("trakt")):
                return None
            return {"type": "movie", "traktId": ids["trakt"], "slug": ids.get("slug"), "tmdbId": mapping["tmdbId"], "url": f"https://trakt.tv/movies/{ids.get('slug')}", "watchable": True}
        show = await trakt_show(mapping["tmdbId"])
        return season_target(mapping, show, c.get("episodes")) if show else None
    except SourceUnavailable as error:
        logger.warning(f"[anime-recommend] Trakt lookup skipped: {error}")
        return None


# ── Picking ──────────────────────────────────────────────────────────────────────────────────


async def _recent_picks(db: AsyncSession, profile_id: str) -> list[AnimePickModel]:
    since = _now().replace(tzinfo=None) - timedelta(days=NO_REPEAT_DAYS)
    return list(await db.scalars(select(AnimePickModel).where(AnimePickModel.profile_id == profile_id, AnimePickModel.shown_at >= since)))


def mode_weights(picks: list[AnimePickModel], modes: tuple[str, ...]) -> dict[str, float]:
    wants = dict.fromkeys(modes, 0)
    skips = dict.fromkeys(modes, 0)
    for pick in picks:
        if pick.mode in wants:
            if pick.action in ("watchlist", "watched"):
                wants[pick.mode] += 1
            elif pick.action == "skip":
                skips[pick.mode] += 1
    weights = {m: CONFIG["initialWeights"][m] * (wants[m] + 1) / (wants[m] + skips[m] + 2) for m in modes}
    total = sum(weights.values())
    return {m: w / total for m, w in weights.items()}


def refunded_ids(picks: list[AnimePickModel], today: str) -> set[str]:
    rated = sorted((p for p in picks if p.day == today and p.own_rating and p.action == "watched"), key=lambda p: p.acted_at or p.shown_at)
    return {p.pick_id for p in rated[:REFUNDS_A_DAY]}


def used_today(picks: list[AnimePickModel], today: str) -> int:
    refunded = refunded_ids(picks, today)
    return sum(1 for p in picks if p.day == today and p.pick_id not in refunded)


def matches(c: dict, filters: dict) -> bool:
    """genre (AniList genre), decade, format (TV / MOVIE / ONA), length (short ≤ 13 episodes,
    cour2 14-26, long > 26)."""
    if filters.get("genre") and filters["genre"] not in c.get("genres", []):
        return False
    decade = filters.get("decade")
    if decade is not None and not (is_integer(c.get("year")) and decade <= c["year"] <= decade + 9):
        return False
    fmt = filters.get("format")
    if fmt and not (c.get("format") == fmt or (fmt == "TV" and c.get("format") == "TV_SHORT")):
        return False
    length, episodes = filters.get("length"), c.get("episodes")
    if length:
        if not is_integer(episodes):
            return False
        if (length == "short" and episodes > 13) or (length == "cour2" and not 14 <= episodes <= 26) or (length == "long" and episodes <= 26):
            return False
    return True


def modes_for(row: MovieSubscriberModel, filters: dict) -> tuple[str, ...]:
    """Airing only when asked; otherwise every pool, airing ones only with the website switch on."""
    if filters.get("airing"):
        return ("airing",)
    return MODES if row.anime_airing else tuple(m for m in MODES if m != "airing")


def _reason(c: dict, mode: str) -> str:
    if c.get("inWatchlist"):
        return "这部在你的想看列表里"
    if mode == "sequel":
        rated = f"你给《{c.get('seedTitle')}》打了 {c['seedRating']:g} 分" if c.get("seedTitle") and is_number(c.get("seedRating")) else "你看完了前一季"
        return f"{rated}，这是它的下一季"
    if mode == "comfort":
        if c.get("source") == "recommendation" and c.get("seedTitle"):
            return f"喜欢《{c['seedTitle']}》的人也推荐这部"
        return f"你喜欢的{'制作公司' if c.get('source') == 'studio' else '监督'} {c.get('person')} 的作品"
    if mode == "bridge":
        return f"你很少看{c.get('blindGenre')}类型，但这部出自你喜欢的 {c.get('person')}"
    if mode == "airing":
        return "本季正在播，跟你的口味很接近"
    if c.get("source") == "decade":
        return f"你很少看 {c.get('blindDecade')} 年代的动画，这是那十年里评价最高的几部之一"
    return f"你很少看{c.get('blindGenre')}类型，这是该类型评价最高的几部之一"


def _ai_prompt(mode: str, candidates: list[dict], summary: dict, filters: dict) -> str:
    liked = "、".join(f"《{x['title']}》{x['rating']:g}分" for x in summary.get("liked") or [] if x.get("title") and is_number(x.get("rating")))
    asked = [
        f"类型 {filters['genre']}" if filters.get("genre") else None,
        f"{filters['decade']} 年代" if filters.get("decade") else None,
        f"形式 {filters['format']}" if filters.get("format") else None,
        {"short": "一季内", "cour2": "两季长度", "long": "长篇"}.get(filters.get("length") or "") or None,
        "只要新番" if filters.get("airing") else None,
    ]
    lines = [
        f"用户口味：看过 {summary.get('watched', '?')} 部动漫，平均打 {summary.get('mean', '?')} 分。",
        f"最常看的类型：{'、'.join(summary.get('genres') or []) or '不明'}。",
        f"喜欢的制作公司：{'、'.join(s for s in summary.get('studios') or [] if s) or '不明'}；喜欢的监督：{'、'.join(d for d in summary.get('directors') or [] if d) or '不明'}。",
        f"打高分的作品：{liked or '不明'}。",
        MODE_INTENT[mode],
        f"用户这次指定的条件：{'、'.join(a for a in asked if a) or '没有'}。",
        "候选（每行：anilistId｜作品名｜年份｜形式/集数｜类型｜制作公司｜AniList 评分/人气｜系统的推荐依据）：",
    ]
    for c in candidates:
        studio = (c.get("studios") or [{}])[0].get("name") or "?"
        lines.append(
            f"{c['anilistId']}｜{c.get('title')}（{c.get('titleEn')}）｜{c.get('year') or '?'}｜{c.get('format')}/{c.get('episodes') or '?'}集｜{'/'.join(c.get('genres') or [])}｜{studio}｜{c.get('score') or '?'}/{c.get('popularity')}｜{_reason(c, mode)}"
        )
    lines.append("请选一部并写理由，只回 JSON。")
    return "\n".join(lines)


async def _ai_choice(db: AsyncSession, row: MovieSubscriberModel, mode: str, candidates: list[dict], summary: dict, filters: dict) -> tuple[dict, str] | None:
    shortlist = sorted(candidates, key=lambda c: -c["score"])[:AI_CANDIDATES]
    if len(shortlist) < 2:
        return None
    request = AiGatewayRequestSchema(
        project="yohaneowo-admin",
        feature="anime-recommend",
        caller=AiCallerSchema(platform="discord", id=row.discord_id, name=row.trakt_name, trigger="recommend"),
        body={"model": AI_MODEL, "max_tokens": 300, "system": AI_SYSTEM, "messages": [{"role": "user", "content": _ai_prompt(mode, shortlist, summary, filters)}]},
    )
    try:
        status, payload = await asyncio.wait_for(ai_gateway.claude(db, request), AI_TIMEOUT_SECONDS)
    except ai_gateway.AiNotConfigured:
        return None
    except Exception as error:  # noqa: BLE001 - the plain sampling takes over
        logger.warning(f"[anime-recommend] AI choice failed: {error}")
        return None
    if status is None or not 200 <= status < 300 or not isinstance(payload, dict):
        return None
    text = "".join(block.get("text", "") for block in payload.get("content") or [] if isinstance(block, dict))
    match = re.search(r"\{.*\}", text, re.S)
    try:
        answer = json.loads(match.group(0)) if match else {}
    except ValueError:
        return None
    chosen = next((c for c in shortlist if str(c["anilistId"]) == str(answer.get("anilistId"))), None)
    reason = str(answer.get("reason") or "").strip()
    if chosen is None or not reason:
        logger.info(f"[anime-recommend] AI answered off-list or empty: {text[:120]!r}")
        return None
    return chosen, reason[:200]


async def card(c: dict) -> dict:
    """What the bot shows: the candidate plus Bangumi's Chinese title, score and summary, the MAL
    score, and where 想看 / 看过了 land on Trakt (None: links only)."""
    deadline = time.monotonic() + 8
    work = {"anilistId": c["anilistId"], "native": c.get("title"), "year": c.get("year"), "bangumiId": None}
    title_zh, bangumi_id, bangumi_score, summary = None, None, None, None
    try:
        matched, _ = await credits._search_matches([work], deadline)  # noqa: SLF001
        found = matched.get(c["anilistId"]) or {}
        bangumi_id, title_zh = found.get("bangumiId"), found.get("nameCn")
        if bangumi_id:
            async with async_db_session() as db, db.begin():
                subject = await bangumi.subject(db, bangumi_id)
            rating = (subject or {}).get("rating") or {}
            bangumi_score = rating.get("score") if is_number(rating.get("score")) and rating.get("score") > 0 else None
            title_zh = title_zh or (subject or {}).get("name_cn") or None
            text = (subject or {}).get("summary")
            summary = text.strip()[:400] if isinstance(text, str) and text.strip() else None
    except SourceUnavailable as error:
        logger.warning(f"[anime-recommend] Bangumi skipped for the card: {error}")
    mal_score = None
    if c.get("malId"):
        scores, _ = await credits._mal_scores([c["malId"]], time.monotonic() + 5)  # noqa: SLF001
        mal_score = (scores.get(str(c["malId"])) or {}).get("score")
    target = await trakt_target(c)
    return {
        "anilistId": c["anilistId"],
        "malId": c.get("malId"),
        "bangumiId": bangumi_id,
        "title": title_zh or c.get("title"),
        "titleNative": c.get("title"),
        "titleEn": c.get("titleEn"),
        "year": c.get("year"),
        "season": c.get("season"),
        "format": c.get("format"),
        "episodes": c.get("episodes"),
        "status": c.get("status"),
        "genres": c.get("genres") or [],
        "tags": (c.get("tags") or [])[:4],
        "studios": [s["name"] for s in c.get("studios") or [] if s.get("name")],
        "cover": c.get("cover"),
        "color": c.get("color"),
        "summary": summary,
        "anilistScore": c.get("score"),
        "bangumiScore": bangumi_score,
        "malScore": mal_score,
        "inWatchlist": bool(c.get("inWatchlist")),
        "links": {
            "anilist": f"https://anilist.co/anime/{c['anilistId']}",
            "mal": f"https://myanimelist.net/anime/{c['malId']}" if c.get("malId") else None,
            "bangumi": f"https://bgm.tv/subject/{bangumi_id}" if bangumi_id else None,
            "trakt": (target or {}).get("url"),
        },
        "trakt": target,
    }


async def pick(db: AsyncSession, row: MovieSubscriberModel, filters: dict, skip_pick_id: str | None = None) -> dict:
    """One recommendation, recorded as a pick. filters: genre, decade, format, length, airing."""
    token = await _token_for(row)
    started = time.monotonic()
    today = today_taipei().isoformat()
    recent = await _recent_picks(db, row.profile_id)
    if skip_pick_id:
        for previous in recent:
            if previous.pick_id == skip_pick_id and previous.action is None:
                previous.action = "skip"
    quota = quota_for(row)
    used = used_today(recent, today)
    if used >= quota:
        raise DailyLimit(next_midnight_taipei())
    recent_ids = {p.anilist_id for p in recent}
    try:
        pools = await _pools_for(row, token)
    except Exception:
        await _report(row.profile_id, None)
        raise
    await _report(row.profile_id, "card")
    rng = random.Random()
    modes = modes_for(row, filters)
    weights = mode_weights(recent, modes)
    first = rng.choices(modes, weights=[weights[m] for m in modes], k=1)
    for mode in first + [m for m in modes if m not in first]:
        candidates = [c for c in pools["pools"].get(mode, []) if c["anilistId"] not in recent_ids and matches(c, filters)]
        if mode == "bridge" and len(candidates) < CONFIG["bridgeMinPoolSize"]:
            continue
        if not candidates:
            continue
        ai = await _ai_choice(db, row, mode, candidates, pools["summary"], filters)
        chosen, ai_reason = ai if ai else (_softmax_pick(candidates, rng), None)
        shown = await card(chosen)
        reason = ai_reason or _reason(chosen, mode)
        pick_row = AnimePickModel(
            profile_id=row.profile_id,
            anilist_id=chosen["anilistId"],
            mode=mode,
            day=today,
            shown_at=_now().replace(tzinfo=None),
            title=(shown["title"] or str(chosen["anilistId"]))[:500],
            reason=reason[:500],
            data=shown,
        )
        db.add(pick_row)
        await db.flush()
        await _report(row.profile_id, None)
        logger.info(
            f"[anime-recommend] Pick for {row.profile_id}: {mode} → {shown['title']} ({chosen['anilistId']}) in {time.monotonic() - started:.1f}s, filters {filters}, {quota - used - 1} left today"
        )
        return {**shown, "pickId": pick_row.pick_id, "mode": mode, "modeLabel": MODE_LABEL[mode], "reason": reason, "ai": bool(ai_reason), "remainingToday": quota - used - 1}
    await _report(row.profile_id, None)
    logger.info(f"[anime-recommend] Nothing to pick for {row.profile_id} with filters {filters}")
    raise NothingToPick()


# ── 想看 / 看过了 / 删除评分 ──────────────────────────────────────────────────────────────────


async def _pick_row(db: AsyncSession, row: MovieSubscriberModel, pick_id: str) -> AnimePickModel:
    found = await db.get(AnimePickModel, pick_id)
    if found is None or found.profile_id != row.profile_id:
        raise LookupError(pick_id)
    return found


def _target_of(found: AnimePickModel) -> dict:
    target = (found.data or {}).get("trakt")
    if not target:
        raise LookupError(found.pick_id)
    return target


def show_payload(target: dict, *, watched_at: str | None = None, rating: int | None = None, rated_at: str | None = None) -> dict:
    """The /sync body for a season (or an episode range of it), or for a film."""
    if target["type"] == "movie":
        movie: dict[str, Any] = {"ids": {"trakt": target["traktId"]}}
        if watched_at:
            movie["watched_at"] = watched_at
        if rating is not None:
            movie.update({"rating": rating, "rated_at": rated_at})
        return {"movies": [movie]}
    season: dict[str, Any] = {"number": target["season"]}
    if rating is not None:
        # Trakt rates whole seasons: a split season's parts share the rating (as on the site).
        season.update({"rating": rating, "rated_at": rated_at})
    elif target.get("episodes"):
        start, end = target["episodes"]
        season["episodes"] = [{"number": n, **({"watched_at": watched_at} if watched_at else {})} for n in range(start, end + 1)]
    elif watched_at:
        season["watched_at"] = watched_at
    return {"shows": [{"ids": {"trakt": target["traktId"]}, "seasons": [season]}]}


async def _drop_from_pool(profile_id: str, anilist_id: int) -> None:
    cached = (await read_cache(POOL_NAMESPACE, [profile_id])).get(profile_id)
    if not cached:
        return
    for mode in MODES:
        cached["pools"][mode] = [c for c in cached["pools"].get(mode, []) if c["anilistId"] != anilist_id]
    await write_cache(POOL_NAMESPACE, {profile_id: cached})


async def _request_sync(profile_id: str) -> None:
    async with async_db_session() as session, session.begin():
        locked = await service.by_id(session, profile_id, lock=True)
        if locked:
            locked.sync_requested = True


async def quota_left(db: AsyncSession, row: MovieSubscriberModel) -> int:
    today = today_taipei().isoformat()
    rows = list(await db.scalars(select(AnimePickModel).where(AnimePickModel.profile_id == row.profile_id, AnimePickModel.day == today)))
    return max(0, quota_for(row) - used_today(rows, today))


async def want(db: AsyncSession, row: MovieSubscriberModel, pick_id: str) -> dict:
    """想看: the season (or film) goes on the Trakt watchlist."""
    token = await _token_for(row)
    found = await _pick_row(db, row, pick_id)
    target = _target_of(found)
    body = {"movies": [{"ids": {"trakt": target["traktId"]}}]} if target["type"] == "movie" else {"shows": [{"ids": {"trakt": target["traktId"]}, "seasons": [{"number": target["season"]}]}]}
    status, _ = await _trakt("POST", "/sync/watchlist", token, json=body)
    if status not in (200, 201):
        raise SourceUnavailable(f"Trakt 暂时无法加入想看列表（HTTP {status}）")
    found.action = "watchlist"
    found.acted_at = _now().replace(tzinfo=None)
    await db.flush()
    await _drop_from_pool(row.profile_id, found.anilist_id)
    logger.info(f"[anime-recommend] 想看 for {row.profile_id}: {found.title} ({found.anilist_id}) → Trakt watchlist")
    return {"pickId": pick_id, "action": "watchlist"}


async def _existing_rating(token: str, target: dict) -> int | None:
    if target["type"] == "movie":
        entries = await _trakt_all("/sync/ratings/movies", token)
        return next((int(e["rating"]) for e in entries if (((e.get("movie") or {}).get("ids") or {}).get("trakt")) == target["traktId"] and is_number(e.get("rating"))), None)
    entries = await _trakt_all("/sync/ratings/seasons", token)
    for entry in entries:
        show = ((entry.get("show") or {}).get("ids") or {}).get("trakt")
        if show == target["traktId"] and (entry.get("season") or {}).get("number") == target["season"] and is_number(entry.get("rating")):
            return int(entry["rating"])
    return None


async def _newest_movie_play(token: str, trakt_id: int) -> int | None:
    status, body = await _trakt("GET", f"/sync/history/movies/{trakt_id}", token, {"limit": 5, "page": 1})
    if status == 200 and isinstance(body, list) and body and is_integer(body[0].get("id")):
        return int(body[0]["id"])
    return None


async def watched(db: AsyncSession, row: MovieSubscriberModel, pick_id: str, rating: int | None) -> dict:
    """看过了. With a rating: the rating and the plays go to Trakt (remembered so they can be undone)
    and the pick is refunded. Without one: a rating already on Trakt marks it watched (no refund);
    otherwise nothing is written and `rated` is False so the bot asks for one."""
    token = await _token_for(row)
    found = await _pick_row(db, row, pick_id)
    target = _target_of(found)
    if not target.get("watchable"):
        raise LookupError(pick_id)
    now = _now().strftime("%Y-%m-%dT%H:%M:%S.000Z")

    async def add_plays() -> None:
        status, _ = await _trakt("POST", "/sync/history", token, json=show_payload(target, watched_at=now))
        if status not in (200, 201):
            raise SourceUnavailable(f"Trakt 暂时无法标记看过（HTTP {status}）")
        found.history_added = True
        if target["type"] == "movie":
            try:
                found.history_id = await _newest_movie_play(token, target["traktId"])
            except SourceUnavailable:
                found.history_id = None

    if rating is None:
        existing = await _existing_rating(token, target)
        if existing is None:
            return {"pickId": pick_id, "title": found.title, "action": found.action, "rated": False}
        if found.action is None:
            await add_plays()
        found.action, found.rating, found.own_rating = "watched_existing", existing, False
        found.acted_at = _now().replace(tzinfo=None)
        await db.flush()
        await _drop_from_pool(row.profile_id, found.anilist_id)
        await _request_sync(row.profile_id)
        return {"pickId": pick_id, "title": found.title, "action": found.action, "rated": True, "rating": existing, "own": False, "refunded": False, "remainingToday": await quota_left(db, row)}
    changing = found.own_rating and found.action == "watched"
    status, _ = await _trakt("POST", "/sync/ratings", token, json=show_payload(target, rating=rating, rated_at=now))
    if status not in (200, 201):
        raise SourceUnavailable(f"Trakt 暂时无法写入评分（HTTP {status}）")
    if not found.history_added:
        await add_plays()
    found.action, found.rating, found.own_rating = "watched", rating, True
    found.acted_at = _now().replace(tzinfo=None)
    await db.flush()
    await _drop_from_pool(row.profile_id, found.anilist_id)
    await _request_sync(row.profile_id)
    refunded = found.pick_id in refunded_ids(await _recent_picks(db, row.profile_id), found.day) and not changing
    logger.info(f"[anime-recommend] 看过了 for {row.profile_id}: {found.title} ({found.anilist_id}) rating {rating}{' (changed)' if changing else ''}{' (refunded)' if refunded else ''}")
    return {"pickId": pick_id, "title": found.title, "action": found.action, "rated": True, "rating": rating, "own": True, "refunded": refunded, "remainingToday": await quota_left(db, row)}


async def unrate(db: AsyncSession, row: MovieSubscriberModel, pick_id: str) -> dict:
    """删除评分: takes back the rating and the plays this pick wrote, and with them the refund."""
    token = await _token_for(row)
    found = await _pick_row(db, row, pick_id)
    if not found.own_rating or found.rating is None:
        raise LookupError(pick_id)
    target = _target_of(found)
    # Removing names the season (or film) alone; Trakt takes the rating off it.
    whole = {"movies": [{"ids": {"trakt": target["traktId"]}}]} if target["type"] == "movie" else {"shows": [{"ids": {"trakt": target["traktId"]}, "seasons": [{"number": target["season"]}]}]}
    status, _ = await _trakt("POST", "/sync/ratings/remove", token, json=whole)
    if status not in (200, 201):
        raise SourceUnavailable(f"Trakt 暂时无法删除评分（HTTP {status}）")
    if found.history_added:
        # The entry was unwatched when picked (watched ones are never picked), so the film's play,
        # the season's plays or those of its episode range all came from this pick.
        if target["type"] == "movie":
            body = {"ids": [found.history_id]} if found.history_id else whole
        else:
            body = show_payload(target) if target.get("episodes") else whole
        status, _ = await _trakt("POST", "/sync/history/remove", token, json=body)
        if status not in (200, 201):
            logger.warning(f"[anime-recommend] plays not removed for {row.profile_id}: HTTP {status}")
    logger.info(f"[anime-recommend] 删除评分 for {row.profile_id}: {found.title} ({found.anilist_id}) rating {found.rating} removed")
    found.action, found.rating, found.own_rating = None, None, False
    found.history_id, found.history_added = None, False
    found.acted_at = _now().replace(tzinfo=None)
    await db.flush()
    await _request_sync(row.profile_id)
    return {"pickId": pick_id, "title": found.title, "action": None, "remainingToday": await quota_left(db, row)}


def pick_out(row: AnimePickModel) -> dict:
    return {
        "card": row.data,
        "pickId": row.pick_id,
        "anilistId": row.anilist_id,
        "mode": row.mode,
        "modeLabel": MODE_LABEL.get(row.mode, row.mode),
        "day": row.day,
        "title": row.title,
        "reason": row.reason,
        "action": row.action,
        "rating": row.rating,
        "ownRating": bool(row.own_rating),
        "shownAt": row.shown_at.isoformat() if row.shown_at else None,
    }


__all__ = ["DailyLimit", "NothingToPick", "TraktRequired", "pick", "want", "watched", "unrate", "pick_out", "quota_for", "progress_of"]
