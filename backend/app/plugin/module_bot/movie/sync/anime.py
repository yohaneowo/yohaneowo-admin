"""Anime series: scores and staff from Bangumi (Chinese), AniList (English) and MyAnimeList.

TMDB keeps a series as one show with seasons; Bangumi, AniList and MAL list each season as its own
entry. Each TMDB season is matched on its own: the show's original (Japanese) title plus the
season's first air date (within WINDOW_DAYS). When the title search misses a later season (they're
often renamed), AniList's SEQUEL relations from the previous season are followed, and Bangumi is
searched again with the title AniList found.

Every season's match (including "not found") is cached: RECENT_CACHE_DAYS for seasons from the
last year, whose scores still move, OLD_CACHE_DAYS for older ones. AniList allows ~30 requests a
minute, so a large history is filled in over several syncs: each sync works for at most
BUDGET_SECONDS (newest shows first) and reports whether anything is left.
"""

import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.database import async_db_session
from app.core.logger import logger

from ..sources import anilist, bangumi, mal
from ..sources.base import SourceUnavailable
from .common import is_integer, is_number, read_cache, write_cache

NAMESPACE = "anime_season"
MAL_NAMESPACE = "mal"
# 2: AniList genres, tags and source were added to the cached entry.
CACHE_VERSION = 2
# AniList tags kept per season: community-voted, this share of voters or more, never spoilers.
TAG_MIN_RANK = 60
TAGS_PER_SEASON = 5
# TMDB puts this on every anime, so it says nothing on the anime tab (Chinese per MOVIE_LANGUAGE).
ANIMATION_GENRES = {"动画", "動畫", "Animation"}
WINDOW_DAYS = 20
SEQUEL_WINDOW_DAYS = 120
RECENT_CACHE_DAYS = 7
OLD_CACHE_DAYS = 90
MAL_CACHE_DAYS = 7
# Consecutive failed MAL lookups before giving up until the next sync.
MAL_MAX_FAILURES = 3
BUDGET_SECONDS = 240
FAVOURITES_PER_ROLE = 8

# The roles shown for anime, in display order.
ROLES = ("director", "original", "studio", "music", "chiefAnimationDirector")
ANILIST_ROLES = {
    "Director": "director",
    "Chief Director": "director",
    "Original Creator": "original",
    "Original Story": "original",
    "Music": "music",
    "Chief Animation Director": "chiefAnimationDirector",
}
BANGUMI_ROLES = {"导演": "director", "总导演": "director", "原作": "original", "音乐": "music", "总作画监督": "chiefAnimationDirector", "动画制作": "studio"}
SERIES_FORMATS = {"TV", "TV_SHORT", "ONA"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _fuzzy(value: dict | None) -> date | None:
    value = value or {}
    try:
        return date(value["year"], value.get("month") or 1, value.get("day") or 1)
    except (KeyError, TypeError, ValueError):
        return None


def _stale(entry: dict | None, today: date) -> bool:
    if not entry or entry.get("cacheVersion") != CACHE_VERSION:
        return True
    aired = _date(entry.get("airDate"))
    days = OLD_CACHE_DAYS if aired and aired < today - timedelta(days=365) else RECENT_CACHE_DAYS
    return str(entry.get("fetchedAt", "")) < (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _score(value: Any, scale: float = 1) -> float | None:
    """A 0–10 score; 0 means nobody rated it."""
    return round(value / scale, 2) if is_number(value) and value > 0 else None


def _same(a: str | None, b: str | None) -> bool:
    def norm(text: str) -> str:
        return re.sub(r"[\s・·.\-]", "", text).casefold()

    return bool(a and b) and norm(a) == norm(b)


# ── Matching one season ──


def slim_anilist(media: dict) -> dict:
    staff = []
    for edge in ((media.get("staff") or {}).get("edges")) or []:
        role = ANILIST_ROLES.get(re.sub(r"\s*\(.*\)$", "", edge.get("role") or "").strip())
        node = edge.get("node") or {}
        name = node.get("name") or {}
        if role and node.get("id") and name.get("full"):
            staff.append({"role": role, "id": node["id"], "en": name["full"], "native": name.get("native")})
    for node in ((media.get("studios") or {}).get("nodes")) or []:
        if node.get("id") and node.get("name"):
            staff.append({"role": "studio", "id": node["id"], "en": node["name"], "native": node["name"]})
    title = media.get("title") or {}
    tags = sorted(
        (t for t in media.get("tags") or [] if is_number(t.get("rank")) and t["rank"] >= TAG_MIN_RANK and t.get("name") and not t.get("isMediaSpoiler") and not t.get("isGeneralSpoiler")),
        key=lambda t: -t["rank"],
    )
    return {
        "id": media.get("id"),
        "idMal": media.get("idMal"),
        "score": _score(media.get("averageScore"), 10),
        "titleEn": title.get("english") or title.get("romaji"),
        "titleNative": title.get("native"),
        # AniList's curated genre list (18 English names), its adaptation source and top tags.
        "genres": [g for g in media.get("genres") or [] if isinstance(g, str)][:10],
        "source": media.get("source") if isinstance(media.get("source"), str) else None,
        "tags": [t["name"] for t in tags[:TAGS_PER_SEASON]],
        "staff": staff,
        "sequels": [
            {"id": node["id"], "start": (_fuzzy(node.get("startDate")) or date.min).isoformat()}
            for edge in ((media.get("relations") or {}).get("edges")) or []
            if edge.get("relationType") == "SEQUEL" and (node := edge.get("node") or {}).get("type") == "ANIME" and node.get("format") in SERIES_FORMATS
        ],
    }


def slim_bangumi(subject: dict, persons: list[dict]) -> dict:
    rating = subject.get("rating") or {}
    staff = []
    for person in persons:
        role = BANGUMI_ROLES.get(person.get("relation") or "")
        if role and person.get("id") and person.get("name"):
            staff.append({"role": role, "id": person["id"], "zh": person["name"]})
    return {
        "id": subject.get("id"),
        "nameCn": subject.get("name_cn") or None,
        "name": subject.get("name"),
        "score": _score(rating.get("score")),
        "votes": rating.get("total") if is_integer(rating.get("total")) else None,
        "staff": staff,
    }


def _window(aired: date, days: int) -> tuple[date, date]:
    return aired - timedelta(days=days), aired + timedelta(days=days)


def _closest(candidates: list[dict], aired: date, start) -> dict | None:
    dated = [(abs((d - aired).days), c) for c in candidates if (d := start(c))]
    return min(dated, key=lambda pair: pair[0])[1] if dated else None


async def _anilist(db, titles: list[str], aired: date, previous: dict | None) -> dict | None:
    low, high = _window(aired, WINDOW_DAYS)
    for title in titles:
        found = await anilist.search(db, title, int(low.strftime("%Y%m%d")), int(high.strftime("%Y%m%d")))
        if found:
            return slim_anilist(found[0])
    sequels = [s for s in (previous or {}).get("sequels") or [] if abs((date.fromisoformat(s["start"]) - aired).days) <= SEQUEL_WINDOW_DAYS]
    best = _closest(sequels, aired, lambda s: date.fromisoformat(s["start"]))
    if best:
        media = await anilist.media(db, best["id"])
        return slim_anilist(media) if media else None
    return None


async def _bangumi(db, titles: list[str], aired: date) -> dict | None:
    low, high = _window(aired, WINDOW_DAYS)
    for title in titles:
        found = await bangumi.search(db, title, low.isoformat(), high.isoformat())
        subject = _closest(found, aired, lambda s: _date(s.get("date")))
        if subject:
            return slim_bangumi(subject, await bangumi.persons(db, subject["id"]))
    return None


async def match_season(item: dict, season: dict, previous: dict | None) -> dict:
    """One TMDB season's AniList and Bangumi entries (either may be None)."""
    aired = _date(season.get("airDate"))
    if aired is None:
        return {"cacheVersion": CACHE_VERSION, "fetchedAt": _now(), "airDate": None, "anilist": None, "bangumi": None}
    base = [t for t in dict.fromkeys([item.get("originalTitle"), item.get("titleEn")]) if t]
    async with async_db_session() as db, db.begin():
        found = await _anilist(db, base, aired, (previous or {}).get("anilist"))
    titles = [t for t in dict.fromkeys([*base, (found or {}).get("titleNative")]) if t]
    async with async_db_session() as db, db.begin():
        subject = await _bangumi(db, titles, aired)
    return {"cacheVersion": CACHE_VERSION, "fetchedAt": _now(), "airDate": season.get("airDate"), "anilist": found, "bangumi": subject}


# ── Filling a section ──


async def _mal_scores(ids: list[int], deadline: float) -> tuple[dict[str, dict], bool]:
    """({MAL ID: {score, votes}}, whether time ran out with scores still to fetch)."""
    keys = [str(i) for i in dict.fromkeys(ids)]
    cached = await read_cache(MAL_NAMESPACE, keys)
    fresh_after = (datetime.now(UTC) - timedelta(days=MAL_CACHE_DAYS)).isoformat()
    fresh: dict[str, dict] = {}
    left = False
    failures = 0
    for key in keys:
        if str((cached.get(key) or {}).get("fetchedAt", "")) >= fresh_after:
            continue
        if time.monotonic() > deadline:
            left = True
            break
        try:
            async with async_db_session() as db, db.begin():
                found = await mal.score(db, int(key))
        except SourceUnavailable as error:
            # One slow answer only skips that ID (tried again next time); a source that keeps
            # failing is down and waits for the regular schedule.
            failures += 1
            if failures >= MAL_MAX_FAILURES:
                logger.warning(f"[movie-sync] MyAnimeList unavailable: {error}")
                break
            continue
        failures = 0
        fresh[key] = {"score": _score((found or {}).get("score")), "votes": (found or {}).get("votes"), "fetchedAt": _now()}
    await write_cache(MAL_NAMESPACE, fresh)
    return {**cached, **fresh}, left


def _classification(item: dict, found: dict) -> dict:
    """The series' genres, tags and source: AniList's when the first season matched, otherwise
    TMDB's genres without the "Animation" every anime carries."""
    genres = [g for g in found.get("genres") or [] if isinstance(g, str)]
    if not genres:
        genres = [g for g in item.get("genres") or [] if g not in ANIMATION_GENRES] or list(item.get("genres") or [])
    return {
        "genre": genres[0] if genres else None,
        "genres": genres[:20],
        "tags": [t for t in found.get("tags") or [] if isinstance(t, str)][:10],
        "source": found.get("source"),
    }


def _genre_rows(items: list[dict], mean: float | None) -> list[dict]:
    """The section's genre summary (same shape as the show section's) from the items' genres."""
    rows: dict[str, dict] = {}
    for item in items:
        rating = item.get("myRating")
        for name in item.get("genres") or []:
            row = rows.get(name) or {"name": name, "count": 0, "weight": 0.0, "ratedCount": 0}
            row["count"] += 1
            if is_number(rating) and mean is not None:
                row["weight"] += rating - mean
                row["ratedCount"] += 1
            rows[name] = row
    total = max(len(items), 1)
    ordered = sorted(rows.values(), key=lambda row: (-row["count"], row["name"]))
    return [
        {
            "id": index + 1,
            "name": row["name"],
            "count": row["count"],
            "ratedCount": row["ratedCount"],
            "interest": row["count"] / total,
            "affinity": row["weight"] / row["ratedCount"] if row["ratedCount"] else None,
            "average": mean + row["weight"] / row["ratedCount"] if row["ratedCount"] and mean is not None else None,
        }
        for index, row in enumerate(ordered)
    ]


def _people(entries: list[dict]) -> list[dict]:
    """Each role's people across the seasons: AniList and Bangumi entries merged on the native name."""
    people: list[dict] = []
    for role in ROLES:
        merged: list[dict] = []
        for entry in entries:
            from_anilist = [p for p in ((entry.get("anilist") or {}).get("staff") or []) if p["role"] == role]
            from_bangumi = [p for p in ((entry.get("bangumi") or {}).get("staff") or []) if p["role"] == role]
            for index, person in enumerate(from_anilist):
                twin = next((p for p in from_bangumi if _same(p["zh"], person.get("native")) or _same(p["zh"], person["en"])), None)
                if twin is None and role == "studio" and index < len(from_bangumi):
                    # Studios go by their English name on AniList and their Japanese one on
                    # Bangumi; both list the main studio first.
                    twin = from_bangumi[index]
                if twin:
                    from_bangumi = [p for p in from_bangumi if p is not twin]
                record = {"role": role, "id": person["id"], "name": twin["zh"] if twin else person.get("native") or person["en"], "nameEn": person["en"]}
                if not any(r["id"] == record["id"] or _same(r["name"], record["name"]) for r in merged):
                    merged.append(record)
            for person in from_bangumi:
                if not any(_same(r["name"], person["zh"]) for r in merged):
                    # Bangumi-only: a negative ID keeps it apart from AniList's.
                    merged.append({"role": role, "id": -person["id"], "name": person["zh"], "nameEn": None})
        people.extend(merged)
    return people


def _favourites(items: list[dict], mean: float | None) -> list[dict]:
    """Staff whose (whole-show rated) series average clearly above the user's mean."""
    if mean is None:
        return []
    records: dict[str, dict] = {}
    for item in items:
        if item.get("myRating") is None:
            continue
        for person in item.get("staff") or []:
            key = f"{person['role']}:{person['id']}"
            record = records.setdefault(key, {"id": person["id"], "name": person["name"], "nameEn": person.get("nameEn"), "role": person["role"], "photo": None, "films": []})
            if not any(f["id"] == item["id"] for f in record["films"]):
                record["films"].append({"id": item["id"], "title": item["title"], "rating": item["myRating"]})
    favourites = []
    for record in records.values():
        count = len(record["films"])
        average = sum(f["rating"] for f in record["films"]) / count
        if count >= 2 and average >= mean + 0.3:
            favourites.append({**record, "count": count, "average": average, "films": sorted(record["films"], key=lambda f: -f["rating"])[:3]})
    return [p for role in ROLES for p in sorted((p for p in favourites if p["role"] == role), key=lambda p: (-p["average"], -p["count"]))[:FAVOURITES_PER_ROLE]]


async def enrich_anime(section: dict | None, budget_seconds: float = BUDGET_SECONDS) -> bool:
    """Add Bangumi / AniList / MAL data to the anime section in place. True when the budget ran out
    with seasons still to look up (the caller syncs again soon)."""
    if not section:
        return False
    deadline = time.monotonic() + budget_seconds
    today = datetime.now(UTC).date()
    seasons = [(item, season) for item in section["items"] for season in item.get("seasons") or [] if _date(season.get("airDate"))]
    cache = await read_cache(NAMESPACE, [f"{item['id']}:{season['number']}" for item, season in seasons])
    pending = False
    unavailable = False
    for item in section["items"]:
        previous = None
        fresh: dict[str, dict] = {}
        for season in sorted(item.get("seasons") or [], key=lambda s: s["number"]):
            key = f"{item['id']}:{season['number']}"
            if not _date(season.get("airDate")):
                continue
            if _stale(cache.get(key), today) and not unavailable:
                if time.monotonic() > deadline:
                    pending = True
                else:
                    try:
                        fresh[key] = cache[key] = await match_season(item, season, previous)
                    except SourceUnavailable as error:
                        # Not cached: tried again at the next sync, without hurrying it.
                        logger.warning(f"[movie-sync] Anime lookup stopped: {error}")
                        unavailable = True
            previous = cache.get(key)
        await write_cache(NAMESPACE, fresh)
    if unavailable:
        pending = False

    mal_ids = [entry["anilist"]["idMal"] for entry in cache.values() if is_integer(((entry or {}).get("anilist") or {}).get("idMal"))]
    mal_scores, mal_left = await _mal_scores(mal_ids, deadline + 60)
    pending = pending or mal_left
    for item in section["items"]:
        entries = []
        for season in item.get("seasons") or []:
            entry = cache.get(f"{item['id']}:{season['number']}") or {}
            found, subject = entry.get("anilist") or {}, entry.get("bangumi") or {}
            scores = mal_scores.get(str(found.get("idMal"))) or {}
            season.update(
                {
                    "anilistId": found.get("id"),
                    "malId": found.get("idMal"),
                    "malScore": scores.get("score"),
                    "bangumiId": subject.get("id"),
                    "bangumiScore": subject.get("score"),
                    "titleZh": subject.get("nameCn"),
                    "titleEn": found.get("titleEn"),
                }
            )
            if entry:
                entries.append(entry)
        first = next((s for s in sorted(item.get("seasons") or [], key=lambda s: s["number"]) if s.get("anilistId") or s.get("bangumiId")), None)
        first_entry = cache.get(f"{item['id']}:{first['number']}") if first else None
        first_scores = mal_scores.get(str(((first_entry or {}).get("anilist") or {}).get("idMal"))) or {}
        item.update(
            {
                # The series' scores are its first season's, as listed on Bangumi / MAL.
                "bangumiId": (first or {}).get("bangumiId"),
                "bangumiScore": (first or {}).get("bangumiScore"),
                "bangumiVotes": ((first_entry or {}).get("bangumi") or {}).get("votes"),
                "malId": (first or {}).get("malId"),
                "malScore": (first or {}).get("malScore"),
                "malVotes": first_scores.get("votes"),
                "anilistId": (first or {}).get("anilistId"),
                "titleEn": (first or {}).get("titleEn") or item.get("titleEn"),
                "staff": _people(entries),
                **_classification(item, (first_entry or {}).get("anilist") or {}),
            }
        )
    section["genres"] = _genre_rows(section["items"], section.get("mean"))
    section["people"] = [p for p in section["people"] if p["role"] in ("creator", "cast")] + _favourites(section["items"], section.get("mean"))
    return pending
