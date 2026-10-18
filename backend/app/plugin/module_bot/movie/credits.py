"""The website's anime credit cards, the anime counterpart of the director card. One card per kind of
credit on the anime tab: a studio (动画制作), a director (监督), an original creator (原作), a
composer (音乐), a chief animation director (总作画监督) or a voice actor (声优).

A card comes in two parts so the site can show what it has as it arrives:
- info(): name, picture and description (Bangumi's Chinese summary, AniList's English one);
- works(): the works so far. The site polls it every few seconds while it says `building` or
  `scoresPending`. A request never waits on the slow parts; it answers from what is cached or being
  built and starts background work for the rest.

Works are AniList's, a page (50) at a time: a studio's main-studio anime, a person's anime in the
card's role only (a director's directing, not their storyboards; an original creator's adaptations,
not the manga), a voice actor's anime roles with the characters. Someone AniList doesn't know is
listed from Bangumi instead. Each work is matched to the person's or studio's Bangumi entries by
native title (width, Roman numerals, spaces and punctuation ignored) for the Chinese title; lists
are cached for CARD_DAYS (PARTIAL_HOURS when Bangumi couldn't be reached).

After the list, a background run looks up what is still missing, newest works first: a Bangumi
search (title and year) for works the list didn't match, and a Bangumi and a MyAnimeList score per
work. Search matches are cached per AniList ID for MATCH_DAYS, Bangumi scores per entry for
SUBJECT_DAYS (shared between cards); MAL scores reuse the anime sync's cache.

Voice actors come from TMDB on the site; their AniList and Bangumi entries are found by name (the
TMDB name and its other spellings, exact matches only) and cached per TMDB ID.
"""

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import async_db_session
from app.core.logger import logger

from .sources import anilist, bangumi
from .sources.base import SourceUnavailable
from .sync.anime import _mal_scores
from .sync.common import is_integer, is_number, read_cache, tmdb_get, write_cache

KINDS = ("studio", "director", "original", "music", "chiefAnimationDirector", "cast")
# AniList's staff roles per card (the part before any "(eps 1-3)").
ANILIST_ROLES = {
    "director": {"Director", "Chief Director"},
    "original": {"Original Creator", "Original Story"},
    "music": {"Music"},
    "chiefAnimationDirector": {"Chief Animation Director"},
}
# Bangumi's relations per card, for people (and studios) AniList doesn't know.
BANGUMI_RELATIONS = {
    "studio": {"动画制作"},
    "director": {"导演", "总导演"},
    "original": {"原作"},
    "music": {"音乐"},
    "chiefAnimationDirector": {"总作画监督"},
}

CARD_NAMESPACE = "credit_card"
INFO_NAMESPACE = "credit_info"
IDENTITY_NAMESPACE = "anime_cast_identity"
MATCH_NAMESPACE = "studio_work_bangumi"
SUBJECT_NAMESPACE = "bangumi_subject_brief"
CARD_DAYS = 7
INFO_DAYS = 7
IDENTITY_DAYS = 30
PARTIAL_HOURS = 6
MATCH_DAYS = 30
MISS_DAYS = 7
SUBJECT_DAYS = 7
BACKGROUND_SECONDS = 1200
MAX_PAGES = 12
# A card neither source can fill is answered "not found" for this long before trying again.
MISSING_SECONDS = 600
EXCLUDED_FORMATS = {"MUSIC"}

MEDIA_FIELDS = "id idMal type format title { romaji english native } startDate { year }"
STUDIO_QUERY = f"""query ($id: Int, $page: Int) {{ Studio(id: $id) {{
  media(isMain: true, sort: [START_DATE_DESC], page: $page, perPage: 50) {{
    pageInfo {{ hasNextPage }} nodes {{ {MEDIA_FIELDS} }} }} }} }}"""
STAFF_QUERY = f"""query ($id: Int, $page: Int) {{ Staff(id: $id) {{
  staffMedia(type: ANIME, sort: [START_DATE_DESC], page: $page, perPage: 50) {{
    pageInfo {{ hasNextPage }} edges {{ staffRole node {{ {MEDIA_FIELDS} }} }} }} }} }}"""
VOICE_QUERY = f"""query ($id: Int, $page: Int) {{ Staff(id: $id) {{
  characterMedia(sort: [START_DATE_DESC], page: $page, perPage: 50) {{
    pageInfo {{ hasNextPage }}
    edges {{ characterRole characters {{ name {{ full native }} }} node {{ {MEDIA_FIELDS} }} }} }} }} }}"""
STUDIO_INFO_QUERY = """query ($id: Int) { Studio(id: $id) { name } }"""
STAFF_INFO_QUERY = """query ($id: Int) { Staff(id: $id) { name { full native } description image { large } } }"""
STAFF_SEARCH_QUERY = """query ($s: String) { Page(perPage: 5) { staff(search: $s) { id name { full native alternative } } } }"""


@dataclass(frozen=True)
class Who:
    """Whose card: the kind and the IDs that find them on AniList and Bangumi."""

    kind: str
    anilist_id: int | None
    bangumi_id: int | None

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.anilist_id or 0}:{self.bangumi_id or 0}"


# Lists being built (key → task) and what they have so far; score top-ups in flight.
_building: dict[str, asyncio.Task] = {}
_partial: dict[str, list[dict]] = {}
_filling: dict[str, asyncio.Task] = {}
_missing: dict[str, float] = {}
_resolving: dict[int, asyncio.Task] = {}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _norm(text: str | None) -> str:
    """Width, Roman numerals (Ⅳ → IV), case, spaces and punctuation don't count."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def _fresh(entry: dict | None, *, days: float = 0, hours: float = 0) -> bool:
    if not entry:
        return False
    return str(entry.get("fetchedAt", "")) >= (datetime.now(UTC) - timedelta(days=days, hours=hours)).isoformat()


def _card_fresh(card: dict | None) -> bool:
    return _fresh(card, hours=PARTIAL_HOURS) if (card or {}).get("partial") else _fresh(card, days=CARD_DAYS)


def _photo(url: Any) -> str | None:
    return url if isinstance(url, str) and url and "/default." not in url else None


async def _anilist(text: str, variables: dict) -> dict:
    async with async_db_session() as db, db.begin():
        return await anilist.query(db, text, variables) or {}


async def _bangumi(call, *args):
    async with async_db_session() as db, db.begin():
        return await call(db, *args)


# ── voice actors: from a TMDB person to AniList and Bangumi ──


async def _names(tmdb_id: int, name: str | None) -> list[str]:
    """The TMDB name, the name the site shows and every other spelling TMDB has."""
    status, person = await tmdb_get(f"/person/{tmdb_id}")
    names = [name] if name else []
    if 200 <= status < 300 and isinstance(person, dict):
        names += [person.get("name"), *(person.get("also_known_as") or [])]
    return [n for n in dict.fromkeys(n.strip() for n in names if isinstance(n, str)) if n]


async def cast_identity(tmdb_id: int, name: str | None) -> tuple[int | None, int | None]:
    """(AniList staff ID, Bangumi person ID) for a TMDB voice actor; exact name matches only."""
    key = str(tmdb_id)
    cached = (await read_cache(IDENTITY_NAMESPACE, [key])).get(key)
    if cached and _fresh(cached, days=IDENTITY_DAYS if (cached.get("anilistId") or cached.get("bangumiId")) else MISS_DAYS):
        return cached.get("anilistId"), cached.get("bangumiId")
    running = _resolving.get(tmdb_id)
    if running is None or running.done():

        async def resolve() -> tuple[int | None, int | None]:
            names = await _names(tmdb_id, name)
            wanted = {_norm(n) for n in names}
            anilist_id = bangumi_id = None
            for candidate in names:
                found = ((await _anilist(STAFF_SEARCH_QUERY, {"s": candidate})).get("Page") or {}).get("staff") or []
                hit = next(
                    (s for s in found if wanted & {_norm(s["name"].get("native")), _norm(s["name"].get("full")), *(_norm(a) for a in s["name"].get("alternative") or [])}),
                    None,
                )
                if hit:
                    anilist_id = hit["id"]
                    break
            try:
                for candidate in names:
                    hit = next((p for p in await _bangumi(bangumi.search_persons, candidate) if _norm(p.get("name")) in wanted), None)
                    if hit:
                        bangumi_id = hit["id"]
                        break
            except SourceUnavailable as error:
                logger.warning(f"[credit-card] Bangumi person search skipped: {error}")
            await write_cache(IDENTITY_NAMESPACE, {key: {"anilistId": anilist_id, "bangumiId": bangumi_id, "fetchedAt": _now()}})
            return anilist_id, bangumi_id

        _resolving[tmdb_id] = running = asyncio.create_task(resolve())
    return await running


# ── info ──


async def info(who: Who, lang: str = "zh") -> dict | None:
    """{name, image, description, format}: description is Bangumi's BBCode (Chinese page) or
    AniList's Markdown (English page); format says which."""
    entry = (await read_cache(INFO_NAMESPACE, [who.key])).get(who.key)
    if not _fresh(entry, days=INFO_DAYS):
        person, staff, studio_name, bangumi_failed = None, None, None, False
        if who.bangumi_id:
            try:
                person = await _bangumi(bangumi.person, who.bangumi_id)
            except SourceUnavailable as error:
                logger.warning(f"[credit-card] Bangumi unavailable: {error}")
                bangumi_failed = True
        if who.anilist_id:
            if who.kind == "studio":
                studio_name = ((await _anilist(STUDIO_INFO_QUERY, {"id": who.anilist_id})).get("Studio") or {}).get("name")
            else:
                staff = (await _anilist(STAFF_INFO_QUERY, {"id": who.anilist_id})).get("Staff")
        if not person and not staff and not studio_name:
            return None
        names = (staff or {}).get("name") or {}
        images = (person or {}).get("images") or {}
        summary = (person or {}).get("summary")
        description_en = (staff or {}).get("description")
        entry = {
            "name": (person or {}).get("name") or names.get("native") or names.get("full") or studio_name,
            "nameEn": names.get("full") or studio_name,
            # Studios: Bangumi's logo. People: AniList's photo, else Bangumi's.
            "image": _photo(images.get("large")) if who.kind == "studio" else _photo(((staff or {}).get("image") or {}).get("large")) or _photo(images.get("large")),
            "description": summary.strip() if isinstance(summary, str) and summary.strip() else None,
            "descriptionEn": description_en.strip() if isinstance(description_en, str) and description_en.strip() else None,
            "fetchedAt": _now(),
        }
        # Without Bangumi's answer the entry is retried at the next look.
        if not bangumi_failed:
            await write_cache(INFO_NAMESPACE, {who.key: entry})
    english = lang == "en"
    return {
        "name": (entry.get("nameEn") if english else None) or entry["name"],
        "image": entry.get("image"),
        "description": entry.get("descriptionEn") if english else entry.get("description"),
        "format": "markdown" if english else "bbcode",
    }


# ── the works list ──


def _work(node: dict, extra: dict | None = None) -> dict | None:
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
        **(extra or {}),
    }


def _base_role(role: Any) -> str:
    return re.sub(r"\s*\(.*\)$", "", role or "").strip()


async def _anilist_pages(who: Who, publish) -> list[dict]:
    """The card's works from AniList, published page by page; deduplicated per anime."""
    query = STUDIO_QUERY if who.kind == "studio" else VOICE_QUERY if who.kind == "cast" else STAFF_QUERY
    works: dict[int, dict] = {}
    for page in range(1, MAX_PAGES + 1):
        data = await _anilist(query, {"id": who.anilist_id, "page": page})
        holder = (data.get("Studio") if who.kind == "studio" else data.get("Staff")) or {}
        media = holder.get("media") or holder.get("staffMedia") or holder.get("characterMedia") or {}
        if who.kind == "studio":
            for node in media.get("nodes") or []:
                if (work := _work(node)) and work["anilistId"] not in works:
                    works[work["anilistId"]] = work
        else:
            for edge in media.get("edges") or []:
                node = edge.get("node") or {}
                if who.kind == "cast":
                    characters = [((c or {}).get("name") or {}).get("native") or ((c or {}).get("name") or {}).get("full") for c in edge.get("characters") or []]
                    main = edge.get("characterRole") == "MAIN"
                    work = works.get(node.get("id")) or _work(node, {"characters": [], "main": False})
                    if not work:
                        continue
                    work["characters"] = list(dict.fromkeys([*work["characters"], *(c for c in characters if c)]))
                    work["main"] = work["main"] or main
                    works[work["anilistId"]] = work
                elif _base_role(edge.get("staffRole")) in ANILIST_ROLES[who.kind] and (work := _work(node)) and work["anilistId"] not in works:
                    works[work["anilistId"]] = work
        publish(list(works.values()))
        if not (media.get("pageInfo") or {}).get("hasNextPage"):
            break
    return list(works.values())


async def _bangumi_entries(who: Who) -> list[dict]:
    """The person's or studio's anime on Bangumi: [{id, name, nameCn, relation, characters}]."""
    if who.kind == "cast":
        roles = await _bangumi(bangumi.person_characters, who.bangumi_id)
        entries: dict[int, dict] = {}
        for role in roles:
            if role.get("subject_type") != bangumi.ANIME or not is_integer(role.get("subject_id")):
                continue
            entry = entries.setdefault(
                role["subject_id"],
                {"id": role["subject_id"], "name": role.get("subject_name"), "nameCn": role.get("subject_name_cn") or None, "relation": "cast", "characters": [], "main": False},
            )
            if role.get("name"):
                entry["characters"].append(role["name"])
            entry["main"] = entry["main"] or role.get("staff") == "主角"
        return list(entries.values())
    subjects = await _bangumi(bangumi.person_subjects, who.bangumi_id)
    return [{"id": s["id"], "name": s.get("name"), "nameCn": s.get("name_cn") or None, "relation": s.get("staff")} for s in subjects if s.get("type") == bangumi.ANIME and is_integer(s.get("id"))]


async def _build(who: Who) -> dict | None:
    """The works list, published to _partial[key] page by page as it grows."""
    entries, partial = [], False
    if who.bangumi_id:
        try:
            entries = await _bangumi_entries(who)
        except SourceUnavailable as error:
            logger.warning(f"[credit-card] Bangumi unavailable: {error}")
            partial = True
    by_name: dict[str, dict] = {}
    for entry in entries:
        by_name.setdefault(_norm(entry["name"]), entry)

    def matched(work: dict) -> dict:
        twin = by_name.get(_norm(work["native"]))
        return {**work, "bangumiId": twin["id"], "title": twin["nameCn"] or work["title"]} if twin else work

    def publish(works: list[dict]) -> None:
        _partial[who.key] = [matched(w) for w in works]

    works = [matched(w) for w in await _anilist_pages(who, publish)] if who.anilist_id else []
    if not works and entries:
        # Known only to Bangumi: its entries in the card's role.
        works = [
            {
                "anilistId": None,
                "malId": None,
                "bangumiId": e["id"],
                "native": e["name"],
                "title": e["nameCn"] or e["name"],
                "titleEn": None,
                "year": None,
                **({"characters": e["characters"], "main": e["main"]} if who.kind == "cast" else {}),
            }
            for e in entries
            if who.kind == "cast" or e["relation"] in BANGUMI_RELATIONS[who.kind]
        ]
    if not works:
        return None
    return {"works": works, "partial": partial, "fetchedAt": _now()}


def _sorted(works: list[dict]) -> list[dict]:
    return sorted(works, key=lambda w: -(w.get("year") or 0))


def _start_build(who: Who) -> asyncio.Task:
    running = _building.get(who.key)
    if running and not running.done():
        return running

    async def build() -> dict | None:
        try:
            card = await _build(who)
            if card is None:
                _missing[who.key] = time.monotonic()
            else:
                await write_cache(CARD_NAMESPACE, {who.key: card})
                _fill_later(who.key, _sorted(card["works"]))
            return card
        except Exception as error:  # noqa: BLE001 - reported to the site as "not found", logged here
            logger.warning(f"[credit-card] building {who.key} failed: {error}")
            _missing[who.key] = time.monotonic()
            return None
        finally:
            _partial.pop(who.key, None)

    task = asyncio.create_task(build())
    _building[who.key] = task
    return task


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
    """{AniList ID: {bangumiId, nameCn}} for works the Bangumi list didn't match, found by a Bangumi
    search on the native title within a year of the AniList start. Also caches the found entry's
    score (search results carry it)."""
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
            found = await _bangumi(bangumi.search, work["native"], f"{year - 1}-01-01", f"{year + 1}-12-31")
        except SourceUnavailable as error:
            logger.warning(f"[credit-card] Bangumi search skipped: {error}")
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
            subject = await _bangumi(bangumi.subject, int(key))
        except SourceUnavailable as error:
            logger.warning(f"[credit-card] Bangumi scores skipped: {error}")
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
            logger.warning(f"[credit-card] background top-up stopped: {error}")

    _filling[key] = asyncio.create_task(fill())


def _filling_now(key: str) -> bool:
    running = _filling.get(key)
    return bool(running and not running.done())


async def works(who: Who, lang: str = "zh") -> dict | None:
    """{works: [{anilistId, bangumiId, title, year, bangumiScore, malScore, characters?, main?}],
    building, scoresPending}: whatever is known right now, newest first. Never waits on a source;
    missing parts are fetched in the background and show up on a later poll. None when nothing is
    known about the card."""
    key = who.key
    card: dict[str, Any] | None = (await read_cache(CARD_NAMESPACE, [key])).get(key)
    building = False
    if _card_fresh(card):
        listed = card["works"]
    elif time.monotonic() - _missing.get(key, -MISSING_SECONDS) < MISSING_SECONDS:
        return None
    else:
        task = _start_build(who)
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
        row = {
            "anilistId": work["anilistId"],
            "bangumiId": work["bangumiId"],
            "title": (work.get("titleEn") if english else None) or work["title"],
            "year": work["year"] or brief.get("year"),
            "bangumiScore": brief.get("score"),
            "malScore": (mal.get(str(work["malId"])) or {}).get("score") if work["malId"] else None,
        }
        if who.kind == "cast":
            row["characters"] = work.get("characters") or []
            row["main"] = bool(work.get("main"))
        out.append(row)
    return {"works": out, "building": building, "scoresPending": building or pending or _filling_now(key)}


# ── warming after a sync ──


async def warm(people: list[Who]) -> None:
    """Build the favourites' cards one after another (each then tops up its scores), so a hover
    finds them ready. Failures only log; a card is built on demand anyway."""
    for who in people:
        try:
            await info(who)
            card = (await read_cache(CARD_NAMESPACE, [who.key])).get(who.key)
            if _card_fresh(card):
                _fill_later(who.key, _sorted(card["works"]))
            else:
                await _start_build(who)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[credit-card] warm-up of {who.key} skipped: {error}")


_warming: set[asyncio.Task] = set()


def warm_later(people: list[Who]) -> None:
    """Fire-and-forget warm(); the sync doesn't wait for it."""
    if not people:
        return
    task = asyncio.create_task(warm(people))
    _warming.add(task)
    task.add_done_callback(_warming.discard)
