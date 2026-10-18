"""The website's studio card (动画制作), the anime counterpart of the director card: the studio's
logo and description from Bangumi, and every anime it was the main studio of (AniList), with
Chinese titles and Bangumi / MyAnimeList scores.

The card is served in two parts so the site can show what it has as it arrives:
- info(): name, logo and description (one Bangumi call, cached INFO_DAYS);
- works(): the works so far. The site polls it every few seconds while it says `building` or
  `scoresPending`. A request never waits on the slow parts; it answers from what is cached or
  being built and starts background work for the rest.

The works list is AniList's (Bangumi lists hundreds of entries per studio, many as a mere
contributor), built a page (50 works) at a time; each work is matched to Bangumi's list of the
studio's entries by native title, with width, Roman numerals, spaces and punctuation ignored, which
gives the Chinese title. A studio known only to Bangumi lists its 动画制作 entries instead. The
list is cached for CARD_DAYS (PARTIAL_HOURS when Bangumi couldn't be reached).

After the list, a background run looks up what is still missing, newest works first: a Bangumi
search (title and year) for the works the list didn't match, and a Bangumi and a MyAnimeList score
per work. Search matches are cached per AniList ID for MATCH_DAYS, Bangumi scores per entry for
SUBJECT_DAYS (shared between studios); MAL scores reuse the anime sync's cache. After each sync
the favourite studios' cards are built the same way, so a hover usually finds them ready.
"""

import asyncio
import re
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import async_db_session
from app.core.logger import logger

from .sources import anilist, bangumi
from .sources.base import SourceUnavailable
from .sync.anime import _mal_scores
from .sync.common import is_integer, is_number, read_cache, write_cache

# v3: every work, matched loosely; scores and search matches kept out of the cached list.
CARD_NAMESPACE = "studio_card_v3"
INFO_NAMESPACE = "studio_info"
MATCH_NAMESPACE = "studio_work_bangumi"
SUBJECT_NAMESPACE = "bangumi_subject_brief"
CARD_DAYS = 7
INFO_DAYS = 7
PARTIAL_HOURS = 6
MATCH_DAYS = 30
MISS_DAYS = 7
SUBJECT_DAYS = 7
BACKGROUND_SECONDS = 1200
# A studio neither source knows is answered "not found" for this long before being looked up again.
MISSING_SECONDS = 600
MAX_PAGES = 12
EXCLUDED_FORMATS = {"MUSIC"}

STUDIO_QUERY = """query ($id: Int, $page: Int) { Studio(id: $id) { id name
  media(isMain: true, sort: [START_DATE_DESC], page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    nodes { id idMal type format title { romaji english native } startDate { year } } } } }"""
NAME_QUERY = """query ($id: Int) { Studio(id: $id) { name } }"""

# Lists being built (key → task) and what they have so far; score top-ups in flight.
_building: dict[str, asyncio.Task] = {}
_partial: dict[str, list[dict]] = {}
_filling: dict[str, asyncio.Task] = {}
_missing: dict[str, float] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _key(anilist_id: int | None, bangumi_id: int | None) -> str:
    return f"{anilist_id or 0}:{bangumi_id or 0}"


def _norm(text: str | None) -> str:
    """Width, Roman numerals (Ⅳ → IV), case, spaces and punctuation don't count."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def _fresh(entry: dict | None, *, days: float = 0, hours: float = 0) -> bool:
    if not entry:
        return False
    return str(entry.get("fetchedAt", "")) >= (datetime.now(UTC) - timedelta(days=days, hours=hours)).isoformat()


def _card_fresh(card: dict | None) -> bool:
    return _fresh(card, hours=PARTIAL_HOURS) if (card or {}).get("partial") else _fresh(card, days=CARD_DAYS)


# ── info ──


async def info(anilist_id: int | None, bangumi_id: int | None, lang: str = "zh") -> dict | None:
    """{name, logo, description}: Bangumi's company entry, else AniList's studio name."""
    key = _key(anilist_id, bangumi_id)
    entry = (await read_cache(INFO_NAMESPACE, [key])).get(key)
    if not _fresh(entry, days=INFO_DAYS):
        person, name = None, None
        if bangumi_id:
            try:
                async with async_db_session() as db, db.begin():
                    person = await bangumi.person(db, bangumi_id)
            except SourceUnavailable as error:
                logger.warning(f"[studio-card] Bangumi unavailable: {error}")
        if anilist_id:
            async with async_db_session() as db, db.begin():
                name = ((await anilist.query(db, NAME_QUERY, {"id": anilist_id}) or {}).get("Studio") or {}).get("name")
        if not person and not name:
            return None
        images = (person or {}).get("images") or {}
        summary = (person or {}).get("summary")
        entry = {
            "name": (person or {}).get("name") or name,
            "nameEn": name,
            "logo": images.get("large") or None,
            "description": summary.strip() if isinstance(summary, str) and summary.strip() else None,
            "fetchedAt": _now(),
        }
        # Without Bangumi's answer the entry is retried at the next look.
        if person or not bangumi_id:
            await write_cache(INFO_NAMESPACE, {key: entry})
    english = lang == "en"
    return {
        "name": (entry.get("nameEn") if english else None) or entry["name"],
        "logo": entry.get("logo"),
        # Bangumi's summary is Chinese (or Japanese); the English card has none to show.
        "description": None if english else entry.get("description"),
    }


# ── the works list ──


def _work(node: dict) -> dict | None:
    if node.get("type") != "ANIME" or node.get("format") in EXCLUDED_FORMATS or not is_integer(node.get("id")):
        return None
    title = node.get("title") or {}
    year = (node.get("startDate") or {}).get("year")
    return {
        "anilistId": node["id"],
        "malId": node.get("idMal") if is_integer(node.get("idMal")) else None,
        "bangumiId": None,
        "native": title.get("native"),
        "title": title.get("native") or title.get("romaji"),
        "titleEn": title.get("english") or title.get("romaji"),
        "year": year if is_integer(year) else None,
    }


async def _bangumi_subjects(bangumi_id: int) -> list[dict]:
    """The studio's anime entries on Bangumi."""
    async with async_db_session() as db, db.begin():
        subjects = await bangumi.person_subjects(db, bangumi_id)
    return [s for s in subjects if s.get("type") == bangumi.ANIME and is_integer(s.get("id"))]


async def _build(key: str, anilist_id: int | None, bangumi_id: int | None) -> dict | None:
    """The works list, published to _partial[key] page by page as it grows."""
    subjects, partial = [], False
    if bangumi_id:
        try:
            subjects = await _bangumi_subjects(bangumi_id)
        except SourceUnavailable as error:
            logger.warning(f"[studio-card] Bangumi unavailable: {error}")
            partial = True
    by_name = {_norm(s.get("name")): s for s in subjects if _norm(s.get("name"))}

    def matched(work: dict) -> dict:
        twin = by_name.get(_norm(work["native"]))
        return {**work, "bangumiId": twin["id"], "title": twin.get("name_cn") or work["title"]} if twin else work

    works: list[dict] = []
    if anilist_id:
        for page in range(1, MAX_PAGES + 1):
            async with async_db_session() as db, db.begin():
                data = await anilist.query(db, STUDIO_QUERY, {"id": anilist_id, "page": page})
            media = ((data or {}).get("Studio") or {}).get("media") or {}
            works.extend(matched(w) for node in media.get("nodes") or [] if (w := _work(node)))
            _partial[key] = list(works)
            if not (media.get("pageInfo") or {}).get("hasNextPage"):
                break
    if not works and subjects:
        # Known only to Bangumi: its 动画制作 entries.
        works = [
            {"anilistId": None, "malId": None, "bangumiId": s["id"], "native": s.get("name"), "title": s.get("name_cn") or s.get("name"), "titleEn": None, "year": None}
            for s in subjects
            if s.get("staff") == "动画制作"
        ]
    if not works:
        return None
    return {"works": works, "partial": partial, "fetchedAt": _now()}


def _start_build(key: str, anilist_id: int | None, bangumi_id: int | None) -> asyncio.Task:
    running = _building.get(key)
    if running and not running.done():
        return running

    async def build() -> dict | None:
        try:
            card = await _build(key, anilist_id, bangumi_id)
            if card is None:
                _missing[key] = time.monotonic()
            else:
                await write_cache(CARD_NAMESPACE, {key: card})
                _fill_later(key, _sorted(card["works"]))
            return card
        except Exception as error:  # noqa: BLE001 - reported to the site as "not found", logged here
            logger.warning(f"[studio-card] building {key} failed: {error}")
            _missing[key] = time.monotonic()
            return None
        finally:
            _partial.pop(key, None)

    task = asyncio.create_task(build())
    _building[key] = task
    return task


def _sorted(works: list[dict]) -> list[dict]:
    return sorted(works, key=lambda w: -(w.get("year") or 0))


# ── scores and late matches ──


def _brief(subject: dict | None) -> dict:
    rating = (subject or {}).get("rating") or {}
    date = str((subject or {}).get("date") or "")
    return {
        "score": rating.get("score") if is_number(rating.get("score")) and rating.get("score") > 0 else None,
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "fetchedAt": _now(),
    }


def _same_title(a: str | None, b: str | None) -> bool:
    x, y = _norm(a), _norm(b)
    return bool(x and y) and (x == y or (min(len(x), len(y)) >= 3 and (x in y or y in x)))


async def _search_matches(works: list[dict], deadline: float) -> tuple[dict[int, dict], bool]:
    """{AniList ID: {bangumiId, nameCn}} for works the studio's Bangumi list didn't match, found by
    a Bangumi search on the native title within a year of the AniList start. Also caches the found
    entry's score (search results carry it)."""
    wanted = [w for w in works if not w["bangumiId"] and w["anilistId"] and w["native"] and w["year"]]
    keys = [str(w["anilistId"]) for w in wanted]
    cached = await read_cache(MATCH_NAMESPACE, keys)
    fresh: dict[str, dict] = {}
    briefs: dict[str, dict] = {}
    left = False
    for work in wanted:
        key = str(work["anilistId"])
        entry = cached.get(key)
        if _fresh(entry, days=MATCH_DAYS if (entry or {}).get("bangumiId") else MISS_DAYS):
            continue
        if time.monotonic() > deadline:
            left = True
            break
        year = work["year"]
        try:
            async with async_db_session() as db, db.begin():
                found = await bangumi.search(db, work["native"], f"{year - 1}-01-01", f"{year + 1}-12-31")
        except SourceUnavailable as error:
            logger.warning(f"[studio-card] Bangumi search skipped: {error}")
            break
        hit = next((s for s in found if is_integer(s.get("id")) and _same_title(s.get("name"), work["native"])), None)
        fresh[key] = {"bangumiId": hit["id"] if hit else None, "nameCn": (hit or {}).get("name_cn") or None, "fetchedAt": _now()}
        if hit:
            briefs[str(hit["id"])] = _brief(hit)
        # Saved as it goes: the site polls while this runs.
        if len(fresh) >= 10:
            await write_cache(MATCH_NAMESPACE, fresh)
            await write_cache(SUBJECT_NAMESPACE, briefs)
            cached.update(fresh)
            fresh, briefs = {}, {}
    await write_cache(MATCH_NAMESPACE, fresh)
    await write_cache(SUBJECT_NAMESPACE, briefs)
    return {int(k): v for k, v in {**cached, **fresh}.items()}, left


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
        fresh[key] = _brief(subject)
        # Saved as it goes: the site polls while this runs.
        if len(fresh) >= 10:
            await write_cache(SUBJECT_NAMESPACE, fresh)
            cached.update(fresh)
            fresh = {}
    await write_cache(SUBJECT_NAMESPACE, fresh)
    return {int(k): v for k, v in {**cached, **fresh}.items()}, left


def _apply_matches(works: list[dict], matches: dict[int, dict]) -> list[dict]:
    out = []
    for work in works:
        match = matches.get(work["anilistId"]) if work["anilistId"] else None
        if not work["bangumiId"] and match and match.get("bangumiId"):
            work = {**work, "bangumiId": match["bangumiId"], "title": match.get("nameCn") or work["title"]}
        out.append(work)
    return out


async def _top_up(works: list[dict], seconds: float) -> tuple[list[dict], dict[int, dict], dict[str, dict], bool]:
    """(works with search matches applied, Bangumi briefs, MAL scores, whether anything is left).
    With seconds <= 0 it only reads the caches."""
    deadline = time.monotonic() + seconds
    # MAL needs no matching, so it runs alongside the Bangumi search and scores.
    mal_task = asyncio.create_task(_mal_scores([w["malId"] for w in works if w["malId"]], deadline))
    matches, match_left = await _search_matches(works, deadline)
    works = _apply_matches(works, matches)
    briefs, bangumi_left = await _subject_briefs([w["bangumiId"] for w in works if w["bangumiId"]], deadline)
    mal, mal_left = await mal_task
    return works, briefs, mal, match_left or bangumi_left or mal_left


def _fill_later(key: str, works: list[dict]) -> None:
    running = _filling.get(key)
    if running and not running.done():
        return

    async def fill() -> None:
        try:
            await _top_up(works, BACKGROUND_SECONDS)
        except Exception as error:  # noqa: BLE001 - a background top-up must never surface
            logger.warning(f"[studio-card] background top-up stopped: {error}")

    _filling[key] = asyncio.create_task(fill())


def _filling_now(key: str) -> bool:
    running = _filling.get(key)
    return bool(running and not running.done())


async def works(anilist_id: int | None, bangumi_id: int | None, lang: str = "zh") -> dict | None:
    """{works: [{anilistId, bangumiId, title, year, bangumiScore, malScore}], building,
    scoresPending}: whatever is known right now, newest first. Never waits on a source; missing
    parts are fetched in the background and show up on a later poll. None when the studio has no
    works anywhere."""
    key = _key(anilist_id, bangumi_id)
    card: dict[str, Any] | None = (await read_cache(CARD_NAMESPACE, [key])).get(key)
    building = False
    if _card_fresh(card):
        listed = card["works"]
    elif time.monotonic() - _missing.get(key, -MISSING_SECONDS) < MISSING_SECONDS:
        return None
    else:
        task = _start_build(key, anilist_id, bangumi_id)
        if task.done():
            card = task.result()
            if card is None:
                return None
            listed = card["works"]
        else:
            building = True
            listed = _partial.get(key) or (card or {}).get("works") or []
    listed_works, briefs, mal, pending = await _top_up(_sorted(listed), 0)
    if pending and not building:
        _fill_later(key, _sorted(listed))
    english = lang == "en"
    out = []
    for work in listed_works:
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
    return {"works": out, "building": building, "scoresPending": building or pending or _filling_now(key)}


# ── warming after a sync ──


async def warm(studios: list[tuple[int | None, int | None]]) -> None:
    """Build the favourite studios' cards one after another (each then tops up its scores), so a
    hover finds them ready. Failures only log; the card is built on demand anyway."""
    for anilist_id, bangumi_id in studios:
        try:
            await info(anilist_id, bangumi_id)
            key = _key(anilist_id, bangumi_id)
            card = (await read_cache(CARD_NAMESPACE, [key])).get(key)
            if _card_fresh(card):
                _fill_later(key, _sorted(card["works"]))
            else:
                await _start_build(key, anilist_id, bangumi_id)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[studio-card] warm-up of {anilist_id}:{bangumi_id} skipped: {error}")


_warming: set[asyncio.Task] = set()


def warm_later(studios: list[tuple[int | None, int | None]]) -> None:
    """Fire-and-forget warm(); the sync doesn't wait for it."""
    if not studios:
        return
    task = asyncio.create_task(warm(studios))
    _warming.add(task)
    task.add_done_callback(_warming.discard)
