"""The website's studio card (动画制作), the anime counterpart of the director card: the studio's
logo and description from Bangumi, and its best-known works from AniList with Bangumi and
MyAnimeList scores.

Works are AniList's most popular entries where the studio is the main studio (Bangumi lists
hundreds of entries per studio, many as a mere contributor), matched to Bangumi by native title for
the Chinese title and score. A studio known only to Bangumi lists its 动画制作 entries instead.
The whole card is cached for CARD_DAYS, or PARTIAL_HOURS when Bangumi couldn't be reached, and
each Bangumi entry's score for SUBJECT_DAYS (shared between studios); MAL scores reuse the anime
sync's cache.
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

CARD_NAMESPACE = "studio_card"
SUBJECT_NAMESPACE = "bangumi_subject_brief"
CARD_DAYS = 7
PARTIAL_HOURS = 6
SUBJECT_DAYS = 7
WORKS = 20
# MAL scores still missing after this many seconds show as "—" and fill in on a later look.
MAL_SECONDS = 12
FORMATS = {"TV", "TV_SHORT", "ONA", "MOVIE", "OVA", "SPECIAL"}

STUDIO_QUERY = """query ($id: Int, $perPage: Int) { Studio(id: $id) { id name
  media(isMain: true, sort: [POPULARITY_DESC], perPage: $perPage) {
    nodes { id idMal type format title { romaji english native } startDate { year } } } } }"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm(text: str | None) -> str:
    return re.sub(r"[\s・·.\-]", "", text or "").casefold()


def _fresh(entry: dict | None, *, days: float = 0, hours: float = 0) -> bool:
    if not entry:
        return False
    return str(entry.get("fetchedAt", "")) >= (datetime.now(UTC) - timedelta(days=days, hours=hours)).isoformat()


async def _anilist_works(anilist_id: int) -> tuple[str | None, list[dict]]:
    async with async_db_session() as db, db.begin():
        data = await anilist.query(db, STUDIO_QUERY, {"id": anilist_id, "perPage": WORKS + 10})
    studio = (data or {}).get("Studio") or {}
    works = []
    for node in ((studio.get("media") or {}).get("nodes")) or []:
        if node.get("type") != "ANIME" or node.get("format") not in FORMATS or not is_integer(node.get("id")):
            continue
        title = node.get("title") or {}
        works.append(
            {
                "anilistId": node["id"],
                "malId": node.get("idMal") if is_integer(node.get("idMal")) else None,
                "bangumiId": None,
                "native": title.get("native"),
                "title": title.get("native") or title.get("romaji"),
                "titleEn": title.get("english") or title.get("romaji"),
                "year": ((node.get("startDate") or {}).get("year")) if is_integer((node.get("startDate") or {}).get("year")) else None,
            }
        )
    return studio.get("name"), works[:WORKS]


async def _bangumi_info(bangumi_id: int) -> tuple[dict | None, list[dict]]:
    """The studio as a Bangumi person (name, logo, summary) and its anime entries."""
    async with async_db_session() as db, db.begin():
        person = await bangumi.person(db, bangumi_id)
        subjects = await bangumi.person_subjects(db, bangumi_id)
    return person, [s for s in subjects if s.get("type") == bangumi.ANIME and is_integer(s.get("id"))]


async def _subject_briefs(ids: list[int]) -> dict[int, dict]:
    """{Bangumi ID: {score, year, nameCn}}, cached per entry; stops at the first outage."""
    keys = [str(i) for i in dict.fromkeys(ids)]
    cached = await read_cache(SUBJECT_NAMESPACE, keys)
    fresh: dict[str, dict] = {}
    for key in keys:
        if _fresh(cached.get(key), days=SUBJECT_DAYS):
            continue
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
            "nameCn": (subject or {}).get("name_cn") or None,
            "fetchedAt": _now(),
        }
    await write_cache(SUBJECT_NAMESPACE, fresh)
    return {int(k): v for k, v in {**cached, **fresh}.items()}


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
            {"anilistId": None, "malId": None, "bangumiId": s["id"], "native": s.get("name"), "title": s.get("name"), "titleEn": None, "year": None} for s in subjects if s.get("staff") == "动画制作"
        ][:WORKS]
    if not works and not person:
        return None
    by_name = {_norm(s.get("name")): s for s in subjects}
    for work in works:
        twin = by_name.get(_norm(work["native"])) if work["bangumiId"] is None else None
        if twin:
            work["bangumiId"] = twin["id"]

    briefs, (mal, _) = await asyncio.gather(
        _subject_briefs([w["bangumiId"] for w in works if w["bangumiId"]]),
        _mal_scores([w["malId"] for w in works if w["malId"]], time.monotonic() + MAL_SECONDS),
    )
    out_works = []
    for work in works:
        brief = (briefs.get(work["bangumiId"]) or {}) if work["bangumiId"] else {}
        out_works.append(
            {
                "anilistId": work["anilistId"],
                "bangumiId": work["bangumiId"],
                "title": brief.get("nameCn") or work["title"],
                "titleEn": work["titleEn"],
                "year": work["year"] or brief.get("year"),
                "bangumiScore": brief.get("score"),
                "malScore": (mal.get(str(work["malId"])) or {}).get("score") if work["malId"] else None,
            }
        )
    out_works.sort(key=lambda w: -(w["year"] or 0))
    images = (person or {}).get("images") or {}
    summary = (person or {}).get("summary")
    return {
        "name": (person or {}).get("name") or name,
        "nameEn": name,
        "logo": images.get("large") or None,
        "description": summary.strip() if isinstance(summary, str) and summary.strip() else None,
        "works": out_works,
        "partial": partial,
        "fetchedAt": _now(),
    }


async def lookup(anilist_id: int | None, bangumi_id: int | None, lang: str = "zh") -> dict | None:
    """{name, logo, description, works: [{anilistId, bangumiId, title, year, bangumiScore, malScore}]}
    in Chinese or (lang "en") English; None when neither source knows the studio."""
    key = f"{anilist_id or 0}:{bangumi_id or 0}"
    card: dict[str, Any] | None = (await read_cache(CARD_NAMESPACE, [key])).get(key)
    if not (_fresh(card, hours=PARTIAL_HOURS) if (card or {}).get("partial") else _fresh(card, days=CARD_DAYS)):
        built = await _build(anilist_id, bangumi_id)
        if built is not None:
            card = built
            await write_cache(CARD_NAMESPACE, {key: built})
    if not card:
        return None
    english = lang == "en"
    return {
        "name": (card.get("nameEn") if english else None) or card["name"],
        "logo": card.get("logo"),
        # Bangumi's summary is Chinese (or Japanese); the English card has none to show.
        "description": None if english else card.get("description"),
        "works": [
            {
                "anilistId": w["anilistId"],
                "bangumiId": w["bangumiId"],
                "title": (w.get("titleEn") if english else None) or w["title"],
                "year": w["year"],
                "bangumiScore": w["bangumiScore"],
                "malScore": w["malScore"],
            }
            for w in card.get("works") or []
        ],
    }
