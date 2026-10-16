"""The website's director card: a TMDB person with biography and directed films, each with public scores.

The card itself (name, photo, biography, films with their TMDB score and IMDb ID) is cached for
CARD_DAYS; IMDb IDs never change and are cached for good. IMDb / Rotten Tomatoes / Metacritic come
from OMDb through its own cache and the daily quota (newest films first), so a card costs OMDb calls
only for films nobody has looked at yet.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.database import async_db_session
from app.core.logger import logger

from .sources import omdb
from .sync.common import read_cache, tmdb_get, write_cache

CARD_NAMESPACE = "person_card"
IMDB_NAMESPACE = "tmdb_imdb"
CARD_DAYS = 7
CONCURRENCY = 4
# A director's films that get public scores; older ones still list, with TMDB's score only.
MAX_SCORED_FILMS = 40
PHOTO_BASE = "https://image.tmdb.org/t/p/w342"


def _biography(person: Any) -> str:
    text = (person or {}).get("biography") if isinstance(person, dict) else None
    return text.strip() if isinstance(text, str) else ""


def directed_films(credits: Any) -> list[dict] | None:
    """Films the person directed, newest release first, undated last, then by title."""
    if not isinstance(credits, dict) or not isinstance(credits.get("crew"), list):
        return None
    films: dict[int, dict] = {}
    for credit in credits["crew"]:
        if not isinstance(credit, dict) or credit.get("job") != "Director":
            continue
        film_id = credit.get("id")
        if not isinstance(film_id, int) or isinstance(film_id, bool) or film_id < 1 or film_id in films:
            continue
        title = next((t.strip() for t in (credit.get("title"), credit.get("original_title")) if isinstance(t, str) and t.strip()), "")
        if not title:
            continue
        date = credit.get("release_date") if isinstance(credit.get("release_date"), str) else ""
        vote = credit.get("vote_average")
        votes = credit.get("vote_count")
        films[film_id] = {
            "id": film_id,
            "title": title,
            "year": int(date[:4]) if date[:4].isdigit() else None,
            "date": date,
            # TMDB averages from a handful of votes are noise.
            "tmdbRating": round(vote, 1) if isinstance(vote, int | float) and isinstance(votes, int) and votes >= 10 else None,
        }
    dated = sorted((f for f in films.values() if f["date"]), key=lambda f: f["title"])
    dated.sort(key=lambda f: f["date"], reverse=True)
    undated = sorted((f for f in films.values() if not f["date"]), key=lambda f: f["title"])
    return [{k: v for k, v in film.items() if k != "date"} for film in dated + undated]


async def _imdb_ids(film_ids: list[int]) -> dict[int, str | None]:
    cached = await read_cache(IMDB_NAMESPACE, [str(i) for i in film_ids])
    found = {int(k): v.get("imdbId") for k, v in cached.items()}
    missing = [i for i in film_ids if i not in found]
    gate = asyncio.Semaphore(CONCURRENCY)
    fresh: dict[str, dict] = {}

    async def one(film_id: int) -> None:
        async with gate:
            try:
                status, body = await tmdb_get(f"/movie/{film_id}/external_ids")
            except Exception as error:  # noqa: BLE001 - one film without an ID just shows no IMDb score
                logger.warning(f"TMDB external_ids {film_id} failed: {error}")
                return
            if 200 <= status < 300 and isinstance(body, dict):
                imdb_id = body.get("imdb_id") if isinstance(body.get("imdb_id"), str) and omdb_id(body["imdb_id"]) else None
                found[film_id] = imdb_id
                fresh[str(film_id)] = {"imdbId": imdb_id}

    await asyncio.gather(*(one(i) for i in missing))
    await write_cache(IMDB_NAMESPACE, fresh)
    return found


def omdb_id(value: str) -> bool:
    return value.startswith("tt") and value[2:].isdigit() and 5 <= len(value) - 2 <= 12


async def _card(person_id: int) -> dict | None:
    cached = (await read_cache(CARD_NAMESPACE, [str(person_id)])).get(str(person_id))
    stale_before = (datetime.now(UTC) - timedelta(days=CARD_DAYS)).isoformat()
    # Cards from before the English interface have no "biographyEn" key.
    if cached and "biographyEn" in cached and str(cached.get("fetchedAt", "")) >= stale_before:
        return cached
    (status, local), (_, credits), (_, english), (_, english_credits) = await asyncio.gather(
        tmdb_get(f"/person/{person_id}", {"language": "zh-TW"}),
        tmdb_get(f"/person/{person_id}/movie_credits", {"language": "zh-TW"}),
        tmdb_get(f"/person/{person_id}", {"language": "en-US"}),
        tmdb_get(f"/person/{person_id}/movie_credits", {"language": "en-US"}),
    )
    if not 200 <= status < 300 or not isinstance(local, dict) or not isinstance(local.get("name"), str):
        return cached
    biography_en = _biography(english)
    biography = _biography(local) or _biography((await tmdb_get(f"/person/{person_id}", {"language": "zh-CN"}))[1]) or biography_en
    films = directed_films(credits)
    english_titles = {f["id"]: f["title"] for f in directed_films(english_credits) or []}
    for film in films or []:
        film["titleEn"] = english_titles.get(film["id"])
    if films:
        ids = await _imdb_ids([f["id"] for f in films[:MAX_SCORED_FILMS]])
        for film in films:
            film["imdbId"] = ids.get(film["id"])
    path = local.get("profile_path")
    card = {
        "id": person_id,
        "name": local["name"],
        "photo": f"{PHOTO_BASE}{path}" if isinstance(path, str) and path.startswith("/") else None,
        "biography": biography or None,
        "biographyEn": biography_en or None,
        "films": films,
        "fetchedAt": datetime.now(UTC).isoformat(),
    }
    await write_cache(CARD_NAMESPACE, {str(person_id): card})
    return card


async def person_id_for_name(name: str) -> int | None:
    """Only an exact name match counts; the top search hit is often someone else."""
    status, found = await tmdb_get("/search/person", {"query": name, "language": "zh-TW"})
    results = found.get("results") if 200 <= status < 300 and isinstance(found, dict) else None
    for person in results if isinstance(results, list) else []:
        if isinstance(person, dict) and name in (person.get("name"), person.get("original_name")):
            person_id = person.get("id")
            return person_id if isinstance(person_id, int) and person_id > 0 else None
    return None


async def lookup(person_id: int | None, name: str | None, lang: str = "zh") -> dict | None:
    """{id, name, photo, biography, films: [{id, title, year, tmdbRating, imdbRating, rottenTomatoes,
    metacritic}] | null}, in Chinese or (lang "en") English; None when the person can't be found."""
    english = lang == "en"
    if person_id is None and name:
        person_id = await person_id_for_name(name)
    if person_id is None:
        return None
    card = await _card(person_id)
    if card is None:
        return None
    films = card.get("films")
    scores: dict = {}
    wanted = [f["imdbId"] for f in (films or [])[:MAX_SCORED_FILMS] if f.get("imdbId")]
    if wanted:
        try:
            async with async_db_session() as db, db.begin():
                scores = await omdb.ratings(db, wanted)
        except Exception as error:  # noqa: BLE001 - the card still shows without public scores
            logger.warning(f"Director card scores unavailable: {error}")
    blank = {"imdbRating": None, "rottenTomatoes": None, "metacritic": None}
    return {
        "id": card["id"],
        "name": card["name"],
        "photo": card["photo"],
        "biography": (card.get("biographyEn") if english else card["biography"]) or (card["biography"] if english else None),
        "films": None
        if films is None
        else [
            {
                "id": f["id"],
                "title": (f.get("titleEn") if english else None) or f["title"],
                "year": f["year"],
                "tmdbRating": f.get("tmdbRating"),
                **{k: (scores.get(f.get("imdbId")) or blank).get(k) for k in blank},
            }
            for f in films
        ],
    }
