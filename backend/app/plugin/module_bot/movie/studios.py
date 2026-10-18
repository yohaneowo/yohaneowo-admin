"""The website's studio card (动画制作), the anime counterpart of the director card: the studio's
logo and description from Bangumi, and every anime it was the main studio of (AniList), with
Chinese titles and Bangumi / MyAnimeList scores.

The works list is AniList's (Bangumi lists hundreds of entries per studio, many as a mere
contributor), matched to Bangumi's list of the studio's entries by native title, which also gives
the Chinese title. A studio known only to Bangumi lists its 动画制作 entries instead. The list is
cached for CARD_DAYS (PARTIAL_HOURS when Bangumi couldn't be reached).

Scores are looked up per entry, which takes far longer than a hover for a big studio: each request
spends up to SCORE_SECONDS on the missing ones, newest first, and leaves the rest to a background
task, so the card fills in over a few looks. Bangumi scores are cached per entry for SUBJECT_DAYS
(shared between studios); MAL scores reuse the anime sync's cache.
"""

import asyncio
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import async_db_session
from app.core.logger import logger

from .sources import anilist, bangumi
from .sources.base import SourceUnavailable
from .sync.anime import _mal_scores
from .sync.common import is_integer, is_number, read_cache, write_cache

# v2: every work (not the 20 most popular), scores kept out of the cached list.
CARD_NAMESPACE = "studio_card_v2"
SUBJECT_NAMESPACE = "bangumi_subject_brief"
CARD_DAYS = 7
PARTIAL_HOURS = 6
SUBJECT_DAYS = 7
SCORE_SECONDS = 12
BACKGROUND_SECONDS = 900
MAX_PAGES = 12
EXCLUDED_FORMATS = {"MUSIC"}

STUDIO_QUERY = """query ($id: Int, $page: Int) { Studio(id: $id) { id name
  media(isMain: true, sort: [START_DATE_DESC], page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    nodes { id idMal type format title { romaji english native } startDate { year } } } } }"""

# Background score lookups in flight, by card key, so repeated looks don't stack them up.
_filling: dict[str, asyncio.Task] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm(text: str | None) -> str:
    return re.sub(r"[\s・·.\-]", "", text or "").casefold()


def _fresh(entry: dict | None, *, days: float = 0, hours: float = 0) -> bool:
    if not entry:
        return False
    return str(entry.get("fetchedAt", "")) >= (datetime.now(UTC) - timedelta(days=days, hours=hours)).isoformat()


async def _anilist_works(anilist_id: int) -> tuple[str | None, list[dict]]:
    name, works = None, []
    for page in range(1, MAX_PAGES + 1):
        async with async_db_session() as db, db.begin():
            data = await anilist.query(db, STUDIO_QUERY, {"id": anilist_id, "page": page})
        studio = (data or {}).get("Studio") or {}
        name = name or studio.get("name")
        media = studio.get("media") or {}
        for node in media.get("nodes") or []:
            if node.get("type") != "ANIME" or node.get("format") in EXCLUDED_FORMATS or not is_integer(node.get("id")):
                continue
            title = node.get("title") or {}
            year = (node.get("startDate") or {}).get("year")
            works.append(
                {
                    "anilistId": node["id"],
                    "malId": node.get("idMal") if is_integer(node.get("idMal")) else None,
                    "bangumiId": None,
                    "native": title.get("native"),
                    "title": title.get("native") or title.get("romaji"),
                    "titleEn": title.get("english") or title.get("romaji"),
                    "year": year if is_integer(year) else None,
                }
            )
        if not (media.get("pageInfo") or {}).get("hasNextPage"):
            break
    return name, works


async def _bangumi_info(bangumi_id: int) -> tuple[dict | None, list[dict]]:
    """The studio as a Bangumi person (name, logo, summary) and its anime entries."""
    async with async_db_session() as db, db.begin():
        person = await bangumi.person(db, bangumi_id)
        subjects = await bangumi.person_subjects(db, bangumi_id)
    return person, [s for s in subjects if s.get("type") == bangumi.ANIME and is_integer(s.get("id"))]


async def _build(anilist_id: int | None, bangumi_id: int | None) -> dict | None:
    name, works = (None, [])
    if anilist_id:
        name, works = await _anilist_works(anilist_id)
    person, subjects, partial = None, [], False
    if bangumi_id:
        try:
            person, subjects = await _bangumi_info(bangumi_id)
        except SourceUnavailable as error:
            logger.warning(f"[studio-card] Bangumi unavailable: {error}")
            partial = True
    if not works and subjects:
        # Known only to Bangumi: its 动画制作 entries.
        works = [
            {"anilistId": None, "malId": None, "bangumiId": s["id"], "native": s.get("name"), "title": s.get("name_cn") or s.get("name"), "titleEn": None, "year": None}
            for s in subjects
            if s.get("staff") == "动画制作"
        ]
    if not works and not person:
        return None
    by_name = {_norm(s.get("name")): s for s in subjects}
    for work in works:
        twin = by_name.get(_norm(work["native"])) if work["bangumiId"] is None else None
        if twin:
            work["bangumiId"] = twin["id"]
            work["title"] = twin.get("name_cn") or work["title"]
    images = (person or {}).get("images") or {}
    summary = (person or {}).get("summary")
    return {
        "name": (person or {}).get("name") or name,
        "nameEn": name,
        "logo": images.get("large") or None,
        "description": summary.strip() if isinstance(summary, str) and summary.strip() else None,
        "works": works,
        "partial": partial,
        "fetchedAt": _now(),
    }


async def _subject_briefs(ids: list[int], deadline: float) -> tuple[dict[int, dict], bool]:
    """({Bangumi ID: {score, year}}, whether some are still to fetch); stops at an outage."""
    keys = [str(i) for i in dict.fromkeys(ids)]
    cached = await read_cache(SUBJECT_NAMESPACE, keys)
    fresh: dict[str, dict] = {}
    left = False
    for key in keys:
        if _fresh(cached.get(key), days=SUBJECT_DAYS):
            continue
        if time.monotonic() > deadline:
            left = True
            break
        try:
            async with async_db_session() as db, db.begin():
                subject = await bangumi.subject(db, int(key))
        except SourceUnavailable as error:
            logger.warning(f"[studio-card] Bangumi scores skipped: {error}")
            break
        rating = (subject or {}).get("rating") or {}
        date = str((subject or {}).get("date") or "")
        fresh[key] = {
            "score": rating.get("score") if is_number(rating.get("score")) and rating.get("score") > 0 else None,
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "fetchedAt": _now(),
        }
        # Saved as it goes, so a long background run isn't lost halfway.
        if len(fresh) >= 20:
            await write_cache(SUBJECT_NAMESPACE, fresh)
            cached.update(fresh)
            fresh = {}
    await write_cache(SUBJECT_NAMESPACE, fresh)
    return {int(k): v for k, v in {**cached, **fresh}.items()}, left


async def _scores(works: list[dict], seconds: float) -> tuple[dict[int, dict], dict[str, dict], bool]:
    deadline = time.monotonic() + seconds
    (briefs, bangumi_left), (mal, mal_left) = await asyncio.gather(
        _subject_briefs([w["bangumiId"] for w in works if w["bangumiId"]], deadline),
        _mal_scores([w["malId"] for w in works if w["malId"]], deadline),
    )
    return briefs, mal, bangumi_left or mal_left


def _fill_later(key: str, works: list[dict]) -> None:
    running = _filling.get(key)
    if running and not running.done():
        return

    async def fill() -> None:
        try:
            await _scores(works, BACKGROUND_SECONDS)
        except Exception as error:  # noqa: BLE001 - a background top-up must never surface
            logger.warning(f"[studio-card] background scores stopped: {error}")

    _filling[key] = asyncio.create_task(fill())


async def lookup(anilist_id: int | None, bangumi_id: int | None, lang: str = "zh") -> dict | None:
    """{name, logo, description, works: [{anilistId, bangumiId, title, year, bangumiScore, malScore}],
    scoresPending} in Chinese or (lang "en") English; None when neither source knows the studio."""
    key = f"{anilist_id or 0}:{bangumi_id or 0}"
    card: dict[str, Any] | None = (await read_cache(CARD_NAMESPACE, [key])).get(key)
    if not (_fresh(card, hours=PARTIAL_HOURS) if (card or {}).get("partial") else _fresh(card, days=CARD_DAYS)):
        built = await _build(anilist_id, bangumi_id)
        if built is not None:
            card = built
            await write_cache(CARD_NAMESPACE, {key: built})
    if not card:
        return None
    works = sorted(card.get("works") or [], key=lambda w: -(w.get("year") or 0))
    briefs, mal, pending = await _scores(works, SCORE_SECONDS)
    if pending:
        _fill_later(key, works)
    english = lang == "en"
    out = []
    for work in works:
        brief = (briefs.get(work["bangumiId"]) or {}) if work["bangumiId"] else {}
        out.append(
            {
                "anilistId": work["anilistId"],
                "bangumiId": work["bangumiId"],
                "title": (work.get("titleEn") if english else None) or work["title"],
                "year": work["year"] or brief.get("year"),
                "bangumiScore": brief.get("score"),
                "malScore": (mal.get(str(work["malId"])) or {}).get("score") if work["malId"] else None,
            }
        )
    return {
        "name": (card.get("nameEn") if english else None) or card["name"],
        "logo": card.get("logo"),
        # Bangumi's summary is Chinese (or Japanese); the English card has none to show.
        "description": None if english else card.get("description"),
        "works": out,
        "scoresPending": pending,
    }
